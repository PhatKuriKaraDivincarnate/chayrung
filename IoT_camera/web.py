import base64
import cv2
import numpy as np
import paho.mqtt.client as mqtt
import mysql.connector
from flask import Flask, render_template, Response, jsonify
from tensorflow.keras.models import load_model
from flask_cors import CORS
import matplotlib.pyplot as plt
import io
import datetime
from flask import send_file

# ----------------- Cấu hình Flask -----------------
app = Flask(__name__)
CORS(app)

# ----------------- Cấu hình MQTT -----------------
MQTT_BROKER = "192.168.1.41"
MQTT_PORT = 1883
MQTT_TOPIC = "img"

image_parts = {}  # Lưu các phần ảnh
latest_image_base64 = ""
total_parts = 0  # Tổng số phần ảnh cần ghép

# ----------------- Cấu hình MySQL -----------------
DB_CONFIG = {
    "host": "quanlybaido.duckdns.org",
    "port": 3306,
    "user": "admin",
    "password": "admin",
    "database": "phathien_chayrung"
}


def get_db_connection():
    return mysql.connector.connect(**DB_CONFIG)


# ----------------- Load mô hình nhận diện cháy -----------------
MODEL_PATH = r"D:\\BT\\IoT_camera\\fire_detection_model.h5"
model = load_model(MODEL_PATH)


def predict_fire(image_data):
    try:
        image_bytes = base64.b64decode(image_data)
        np_arr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

        if img is None:
            print("🔥 Lỗi: Ảnh không hợp lệ!")
            return 0

        img = cv2.resize(img, (224, 224)) / 255.0
        img = np.expand_dims(img, axis=0)
        prediction = model.predict(img)
        return int(prediction[0][0] > 0.5)
    except Exception as e:
        print(f"🔥 Lỗi khi dự đoán cháy: {e}")
        return 0


def save_to_database(fire_status):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO fire_db (thong_tin, times) VALUES (%s, NOW())", (fire_status,))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"🔥 Lỗi khi lưu vào DB: {e}")


# ----------------- Xử lý MQTT -----------------
# ----------------- Xử lý MQTT -----------------
def on_message(client, userdata, msg):
    global image_parts, total_parts, latest_image_base64
    try:
        message = msg.payload.decode().strip()

        if ":" not in message or "/" not in message.split(":")[0]:
            print("📦 Đã nhận được ảnh hoàn chỉnh...")
            return

        part_info, part_data = message.split(":", 1)
        part_number, total_parts_received = map(int, part_info.split("/"))

        image_parts[part_number] = part_data
        total_parts = total_parts_received

        if len(image_parts) == total_parts:
            full_image_base64 = "".join(image_parts[i] for i in sorted(image_parts.keys()))
            full_image_base64 = full_image_base64 + "=" * ((4 - len(full_image_base64) % 4) % 4)
            latest_image_base64 = full_image_base64
            image_parts.clear()
            total_parts = 0
            print("✅ Ảnh đã nhận đầy đủ!")

            # 🔥 Dự đoán cháy
            fire_status = predict_fire(full_image_base64)

            # 💾 Lưu kết quả vào MySQL
            save_to_database(fire_status)
            print(f"🔥 Kết quả nhận diện: {'Có cháy' if fire_status else 'Không có cháy'}")

    except Exception as e:
        print(f"🔥 Lỗi trong xử lý MQTT: {e}")



client = mqtt.Client()
client.on_message = on_message
client.connect(MQTT_BROKER, MQTT_PORT, 60)
client.subscribe(MQTT_TOPIC)
client.loop_start()


# ----------------- Flask Routes -----------------
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/latest-image')
def latest_image():
    if latest_image_base64:
        try:
            base64_fixed = latest_image_base64 + "=" * ((4 - len(latest_image_base64) % 4) % 4)
            image_bytes = base64.b64decode(base64_fixed)
            return Response(image_bytes, mimetype='image/jpeg')
        except Exception as e:
            return jsonify({"error": f"Lỗi giải mã ảnh: {e}"}), 500
    return jsonify({"error": "Không có ảnh"}), 404


@app.route('/latest-fire-status', methods=['GET'])
def latest_fire_status():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT thong_tin, times FROM fire_db ORDER BY times DESC LIMIT 1")
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if row:
            return jsonify({"fire_status": row[0], "timestamp": row[1].strftime("%Y-%m-%d %H:%M:%S")})
        else:
            return jsonify({"error": "Không có dữ liệu"}), 404
    except mysql.connector.Error as sql_error:
        return jsonify({"error": f"Lỗi MySQL: {sql_error}"}), 500
    except Exception as e:
        return jsonify({"error": f"Lỗi hệ thống: {e}"}), 500

@app.route('/fire-history')
def fire_history():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT thong_tin, times FROM fire_db ORDER BY times ASC")
        data = cursor.fetchall()
        cursor.close()
        conn.close()

        times = [row[1].strftime("%Y-%m-%d %H:%M:%S") for row in data]
        statuses = [row[0] for row in data]

        return jsonify({"times": times, "statuses": statuses})
    except Exception as e:
        return jsonify({"error": f"Lỗi khi lấy lịch sử cháy: {e}"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
