import base64
import os
import shutil
import threading
import time

import cv2
import numpy as np
from flask import Flask, jsonify, request
from flask_cors import CORS
from train import train_model

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATASET_ROOT = os.path.join(BASE_DIR, "dataset")
TRAINER_DIR = os.path.join(BASE_DIR, "trainer")
TRAINER_PATH = os.path.join(TRAINER_DIR, "trainer.yml")

app = Flask(__name__)

# Konfigurasi CORS: di production bisa dibatasi via env CORS_ORIGINS (pisah koma)
cors_origins_raw = os.getenv("CORS_ORIGINS", "*").strip()
cors_origins = "*" if cors_origins_raw == "*" else [o.strip() for o in cors_origins_raw.split(",") if o.strip()]
CORS(app, resources={r"/*": {"origins": cors_origins}})

# Global status tracker untuk proses registrasi
# Format: { user_id: { 'status': 'idle'|'capturing'|'training'|'completed'|'failed', 'current': 0, 'total': 100, 'message': '' } }
captures_status = {}
status_lock = threading.Lock()
model_lock = threading.Lock()

# Load Haar Cascade sekali saat startup agar cepat
FACE_CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
face_cascade = cv2.CascadeClassifier(FACE_CASCADE_PATH)
# CLAHE adaptif dengan clipLimit 3.0 untuk menstabilkan kondisi cahaya backlight / berlebih
clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))

# Recognizer global untuk attendance
recognizer = None
has_model = False


def load_recognizer():
    global recognizer, has_model
    with model_lock:
        try:
            rec = cv2.face.LBPHFaceRecognizer_create()
        except AttributeError:
            print("[ERROR] cv2.face tidak ditemukan. Pastikan opencv-contrib-python terinstall.")
            recognizer = None
            has_model = False
            return False

        if os.path.exists(TRAINER_PATH):
            try:
                rec.read(TRAINER_PATH)
                recognizer = rec
                has_model = True
                print("[INFO] Model trainer.yml berhasil dimuat.")
                return True
            except Exception as e:
                print(f"[ERROR] Gagal membaca model: {str(e)}")
        else:
            print("[WARNING] trainer.yml tidak ditemukan. Lakukan registrasi wajah terlebih dahulu.")

        recognizer = rec
        has_model = False
        return False


load_recognizer()


def decode_uploaded_frame():
    """Decode gambar dari multipart form-data ('image') atau JSON base64 ('image')."""
    raw_bytes = None
    if "image" in request.files:
        raw_bytes = request.files["image"].read()
    else:
        payload = request.get_json(silent=True) or {}
        image_data = payload.get("image")
        if isinstance(image_data, str) and image_data:
            if "," in image_data:
                image_data = image_data.split(",", 1)[1]
            raw_bytes = base64.b64decode(image_data)

    if not raw_bytes:
        return None

    np_arr = np.frombuffer(raw_bytes, np.uint8)
    frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    return frame


def preprocess_face(gray, x, y, w, h, apply_clahe=False):
    """Pangkas 10% bagian atas wajah (rambut/dahi) lalu resize ke 200x200."""
    hair_offset = int(h * 0.10)
    y_cropped = min(y + hair_offset, y + h - 1)
    h_cropped = max(1, h - hair_offset)
    face_crop = gray[y_cropped : y_cropped + h_cropped, x : x + w]
    face_resized = cv2.resize(face_crop, (200, 200))
    if apply_clahe:
        # Gunakan kombinasi CLAHE + normalisasi min-max agar tahan silau / cahaya berlebih
        face_resized = clahe.apply(face_resized)
        face_resized = cv2.normalize(face_resized, None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX)
    return face_resized


def run_training_async(user_id):
    try:
        train_res = train_model(dataset_dir=DATASET_ROOT, trainer_dir=TRAINER_DIR, model_name="trainer.yml")
        if train_res.get("success"):
            load_recognizer()
            with status_lock:
                if user_id in captures_status:
                    captures_status[user_id]["status"] = "completed"
                    captures_status[user_id]["message"] = "Registrasi wajah dan training selesai!"
        else:
            with status_lock:
                if user_id in captures_status:
                    captures_status[user_id]["status"] = "failed"
                    captures_status[user_id]["message"] = f"Training gagal: {train_res.get('message', 'Unknown error')}"
    except Exception as e:
        with status_lock:
            if user_id in captures_status:
                captures_status[user_id]["status"] = "failed"
                captures_status[user_id]["message"] = f"Error training: {str(e)}"


@app.route("/")
def index():
    return jsonify({
        "status": "online",
        "mode": "browser-frame-api",
        "has_model": has_model,
        "message": "Python Face Recognition Service is running"
    })


@app.route("/api/register-frame/<int:user_id>", methods=["POST"])
def register_frame(user_id):
    total_samples = 100
    frame = decode_uploaded_frame()
    if frame is None:
        return jsonify({"success": False, "message": "Frame gambar tidak valid."}), 400

    dataset_dir = os.path.join(DATASET_ROOT, str(user_id))

    with status_lock:
        current_state = captures_status.get(user_id)
        if not current_state or current_state.get("status") in ("idle", "failed", "completed"):
            if os.path.exists(dataset_dir):
                shutil.rmtree(dataset_dir)
            os.makedirs(dataset_dir, exist_ok=True)
            captures_status[user_id] = {
                "status": "capturing",
                "current": 0,
                "total": total_samples,
                "message": "Mengambil sampel wajah..."
            }
        elif current_state.get("status") == "training":
            return jsonify({**current_state, "face_detected": False, "box": None})

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    
    # Deteksi multi-scale pada frame standar
    faces = face_cascade.detectMultiScale(
        gray,
        scaleFactor=1.2,
        minNeighbors=4,
        minSize=(100, 100),
    )
    
    # Jika wajah belum terdeteksi (karena silau, backlight, atau cahaya berlebih),
    # coba gunakan CLAHE enhancement agar fitur wajah (mata, alis, bibir) tetap terlihat
    if len(faces) == 0:
        gray_enhanced = clahe.apply(gray)
        faces = face_cascade.detectMultiScale(
            gray_enhanced,
            scaleFactor=1.2,
            minNeighbors=4,
            minSize=(100, 100),
        )
        if len(faces) > 0:
            gray = gray_enhanced

    faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)

    face_detected = len(faces) > 0
    box = None

    if face_detected:
        x, y, w, h = [int(v) for v in faces[0]]
        box = [x, y, w, h]
        # Gunakan normalisasi cahaya (apply_clahe=True) agar dataset yang tersimpan tahan silau
        face_resized = preprocess_face(gray, x, y, w, h, apply_clahe=True)

        should_train = False
        with status_lock:
            state = captures_status[user_id]
            if state["status"] == "capturing" and state["current"] < total_samples:
                state["current"] += 1
                count = state["current"]
                img_path = os.path.join(dataset_dir, f"user_{user_id}_{count}.jpg")
                cv2.imwrite(img_path, face_resized)
                state["message"] = f"Mengambil sampel wajah: {count}/{total_samples}"

                if count >= total_samples:
                    state["status"] = "training"
                    state["message"] = "Melakukan training model wajah (LBPH)..."
                    should_train = True

        if should_train:
            threading.Thread(target=run_training_async, args=(user_id,), daemon=True).start()

    with status_lock:
        response_state = dict(captures_status[user_id])

    return jsonify({
        **response_state,
        "face_detected": face_detected,
        "box": box,
    })


@app.route("/status/<int:user_id>")
def get_status(user_id):
    with status_lock:
        status = captures_status.get(
            user_id,
            {
                "status": "idle",
                "current": 0,
                "total": 100,
                "message": "Belum memulai.",
            },
        )
    return jsonify(status)


@app.route("/reset/<int:user_id>", methods=["POST"])
def reset_status(user_id):
    with status_lock:
        captures_status[user_id] = {
            "status": "idle",
            "current": 0,
            "total": 100,
            "message": "State direset.",
        }
    return jsonify({"success": True, "message": f"Status untuk user {user_id} telah direset"})


@app.route("/train", methods=["POST"])
def manual_train():
    res = train_model(dataset_dir=DATASET_ROOT, trainer_dir=TRAINER_DIR, model_name="trainer.yml")
    if res.get("success"):
        load_recognizer()
    return jsonify(res)


# ==========================================
# ABSENSI SCAN WAJAH (RECOGNITION)
# ==========================================
REQUIRED_VOTES = 1
MAX_AVG_CONFIDENCE = 55.0
MAX_FRAME_CONFIDENCE = 55.0

attendance_sessions = {}
attendance_lock = threading.Lock()


def get_session_state(session_id):
    if session_id not in attendance_sessions:
        attendance_sessions[session_id] = {
            "locked": False,
            "vote": {"user_id": None, "count": 0, "confidences": []},
            "last_recognition": {"user_id": None, "confidence": 0.0, "timestamp": 0},
        }
    return attendance_sessions[session_id]


@app.route("/api/attendance-frame", methods=["POST"])
def attendance_frame():
    frame = decode_uploaded_frame()
    if frame is None:
        return jsonify({"success": False, "message": "Frame gambar tidak valid."}), 400

    session_id = request.form.get("session_id") or (request.get_json(silent=True) or {}).get("session_id") or "default"

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    
    # Deteksi multi-scale cepat dengan minSize 80
    faces = face_cascade.detectMultiScale(
        gray,
        scaleFactor=1.2,
        minNeighbors=3,
        minSize=(80, 80),
    )
    
    # Jika wajah belum terdeteksi karena silau/overexposed, coba deteksi dengan CLAHE
    if len(faces) == 0:
        gray_enhanced = clahe.apply(gray)
        faces = face_cascade.detectMultiScale(
            gray_enhanced,
            scaleFactor=1.2,
            minNeighbors=4,
            minSize=(120, 120),
        )
        if len(faces) > 0:
            gray = gray_enhanced
    faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)

    if len(faces) == 0:
        with attendance_lock:
            state = get_session_state(session_id)
            if state["vote"]["count"] > 0:
                state["vote"] = {"user_id": None, "count": 0, "confidences": []}
        return jsonify({
            "has_model": has_model,
            "face_detected": False,
            "box": None,
            "recognized": False,
            "user_id": None,
            "confidence": None,
            "vote_count": 0,
            "required_votes": REQUIRED_VOTES,
            "label": "Tidak ada wajah",
        })

    x, y, w, h = [int(v) for v in faces[0]]
    box = [x, y, w, h]

    if not has_model or recognizer is None:
        return jsonify({
            "has_model": False,
            "face_detected": True,
            "box": box,
            "recognized": False,
            "user_id": None,
            "confidence": None,
            "vote_count": 0,
            "required_votes": REQUIRED_VOTES,
            "label": "Model Belum Ditraining",
        })

    face_resized = preprocess_face(gray, x, y, w, h, apply_clahe=True)
    with model_lock:
        label_id, confidence = recognizer.predict(face_resized)

    recognized_user_id = None
    recognized_conf = None
    vote_count = 0
    is_match = float(confidence) < MAX_FRAME_CONFIDENCE
    label_txt = "Tidak Dikenal"

    with attendance_lock:
        state = get_session_state(session_id)
        if is_match:
            if not state["locked"]:
                if state["vote"]["user_id"] == int(label_id):
                    state["vote"]["count"] += 1
                    state["vote"]["confidences"].append(float(confidence))
                else:
                    state["vote"] = {
                        "user_id": int(label_id),
                        "count": 1,
                        "confidences": [float(confidence)],
                    }

                vote_count = state["vote"]["count"]
                label_txt = f"ID:{label_id} ({round(confidence, 1)}) [{vote_count}/{REQUIRED_VOTES}]"

                if state["vote"]["count"] >= REQUIRED_VOTES:
                    avg_conf = sum(state["vote"]["confidences"]) / len(state["vote"]["confidences"])
                    if avg_conf < MAX_AVG_CONFIDENCE:
                        state["last_recognition"] = {
                            "user_id": int(label_id),
                            "confidence": float(avg_conf),
                            "timestamp": time.time(),
                        }
                        state["locked"] = True
                        state["vote"] = {"user_id": None, "count": 0, "confidences": []}
                        recognized_user_id = int(label_id)
                        recognized_conf = float(avg_conf)
                        print(f"[ABSENSI] User {label_id} terdeteksi, avg confidence: {avg_conf:.1f}")
                    else:
                        print(f"[REJECT] User {label_id} ditolak, avg confidence: {avg_conf:.1f} > {MAX_AVG_CONFIDENCE}")
                        state["vote"] = {"user_id": None, "count": 0, "confidences": []}
            else:
                label_txt = f"ID:{label_id} (Memproses...)"
        else:
            if state["vote"]["count"] > 0:
                state["vote"] = {"user_id": None, "count": 0, "confidences": []}

    return jsonify({
        "has_model": True,
        "face_detected": True,
        "box": box,
        "recognized": recognized_user_id is not None,
        "user_id": recognized_user_id,
        "confidence": recognized_conf,
        "is_match": is_match,
        "vote_count": vote_count,
        "required_votes": REQUIRED_VOTES,
        "label": label_txt,
    })


@app.route("/attendance-status")
def get_attendance_status():
    session_id = request.args.get("session_id", "default")
    now = time.time()
    with attendance_lock:
        state = get_session_state(session_id)
        last = state["last_recognition"]
        if last["user_id"] is not None and (now - last["timestamp"] < 3.0):
            return jsonify({
                "user_id": last["user_id"],
                "confidence": last["confidence"],
            })
    return jsonify({"user_id": None})


@app.route("/attendance-unlock", methods=["POST"])
def attendance_unlock():
    payload = request.get_json(silent=True) or {}
    session_id = payload.get("session_id", "default")
    with attendance_lock:
        state = get_session_state(session_id)
        state["last_recognition"] = {"user_id": None, "confidence": 0.0, "timestamp": 0}
        state["locked"] = False
        state["vote"] = {"user_id": None, "count": 0, "confidences": []}
    return jsonify({"success": True, "message": "Recognition lock dilepas"})


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    if debug:
        app.run(host="0.0.0.0", port=port, debug=True)
    else:
        from waitress import serve
        print(f"[INFO] Menjalankan production WSGI server (Waitress) di 0.0.0.0:{port}")
        serve(app, host="0.0.0.0", port=port, threads=8)
