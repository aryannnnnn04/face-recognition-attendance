# app.py
# Face Recognition Attendance System - Main Application

import cv2
import face_recognition
import numpy as np
import sqlite3
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta
import os
import pickle
import json
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, Response
from werkzeug.utils import secure_filename
import threading
import time
from contextlib import contextmanager

# --- Database Management Class ---
class DatabaseManager:
    def __init__(self, db_path="attendance_system.db"):
        self.db_path = db_path
        self.init_database()
    
    @contextmanager
    def get_db_connection(self):
        conn = sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def init_database(self):
        with self.get_db_connection() as conn:
            # Create employees table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS employees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_id TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    email TEXT,
                    department TEXT,
                    face_encoding TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create attendance table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS attendance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_id TEXT NOT NULL,
                    check_in_time TIMESTAMP,
                    check_out_time TIMESTAMP,
                    date DATE NOT NULL,
                    status TEXT DEFAULT 'present',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (employee_id) REFERENCES employees (employee_id)
                )
            ''')
            conn.commit()
    
    def add_employee(self, employee_id, name, email, department, face_encoding):
        with self.get_db_connection() as conn:
            try:
                encoding_str = json.dumps(face_encoding.tolist())
                conn.execute('''
                    INSERT INTO employees (employee_id, name, email, department, face_encoding)
                    VALUES (?, ?, ?, ?, ?)
                ''', (employee_id, name, email, department, encoding_str))
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False
    
    def get_all_employees(self):
        with self.get_db_connection() as conn:
            cursor = conn.execute('SELECT * FROM employees ORDER BY name')
            return cursor.fetchall()
    
    def get_employee_encodings(self):
        with self.get_db_connection() as conn:
            cursor = conn.execute('SELECT employee_id, name, face_encoding FROM employees')
            employees = cursor.fetchall()
            
            encodings = []
            names = []
            employee_ids = []
            
            for emp in employees:
                encoding = np.array(json.loads(emp['face_encoding']))
                encodings.append(encoding)
                names.append(emp['name'])
                employee_ids.append(emp['employee_id'])
            
            return encodings, names, employee_ids
    
    def mark_attendance(self, employee_id, attendance_type='check_in'):
        with self.get_db_connection() as conn:
            today = datetime.now().date()
            current_time = datetime.now()
            
            cursor = conn.execute('SELECT * FROM attendance WHERE employee_id = ? AND date = ?', (employee_id, today))
            existing_record = cursor.fetchone()
            
            if existing_record:
                if attendance_type == 'check_out' and existing_record['check_out_time'] is None:
                    conn.execute('UPDATE attendance SET check_out_time = ? WHERE employee_id = ? AND date = ?', (current_time, employee_id, today))
                    conn.commit()
                    return {'success': True, 'message': 'Check-out recorded successfully'}
                else:
                    return {'success': False, 'message': 'Attendance already recorded for today'}
            else:
                if attendance_type == 'check_in':
                    conn.execute('INSERT INTO attendance (employee_id, check_in_time, date) VALUES (?, ?, ?)', (employee_id, current_time, today))
                    conn.commit()
                    return {'success': True, 'message': 'Check-in recorded successfully'}
                else:
                    return {'success': False, 'message': 'Must check-in first'}
    
    def get_attendance_records(self, days=7):
        with self.get_db_connection() as conn:
            start_date = datetime.now().date() - timedelta(days=days)
            cursor = conn.execute('''
                SELECT a.*, e.name, e.department
                FROM attendance a
                JOIN employees e ON a.employee_id = e.employee_id
                WHERE a.date >= ?
                ORDER BY a.date DESC, a.check_in_time DESC
            ''', (start_date,))
            return cursor.fetchall()

    def get_attendance_summary(self):
        with self.get_db_connection() as conn:
            today = datetime.now().date()
            
            cursor = conn.execute('SELECT COUNT(*) as present_today FROM attendance WHERE date = ? AND check_in_time IS NOT NULL', (today,))
            present_today = cursor.fetchone()['present_today']
            
            cursor = conn.execute('SELECT COUNT(*) as total FROM employees')
            total_employees = cursor.fetchone()['total']
            
            week_start = today - timedelta(days=7)
            cursor = conn.execute('''
                SELECT AVG(daily_count) as avg_attendance
                FROM (
                    SELECT DATE(date) as day, COUNT(*) as daily_count
                    FROM attendance 
                    WHERE date >= ? AND check_in_time IS NOT NULL
                    GROUP BY DATE(date)
                )
            ''', (week_start,))
            avg_result = cursor.fetchone()
            avg_attendance = round(avg_result['avg_attendance'] or 0, 1)
            
            return {
                'present_today': present_today,
                'total_employees': total_employees,
                'absent_today': total_employees - present_today,
                'avg_attendance': avg_attendance
            }

# --- Face Recognition System Class ---
class FaceRecognitionSystem:
    def __init__(self):
        self.db_manager = DatabaseManager()
        self.known_face_encodings = []
        self.known_face_names = []
        self.known_employee_ids = []
        self.load_known_faces()
        self.camera = None
        self.is_running = False
        self.lock = threading.Lock()
        
    def load_known_faces(self):
        encodings, names, emp_ids = self.db_manager.get_employee_encodings()
        self.known_face_encodings = encodings
        self.known_face_names = names
        self.known_employee_ids = emp_ids
        print(f"Loaded {len(self.known_face_encodings)} face encodings from database")
    
    def add_new_employee(self, employee_id, name, email, department, image_path):
        try:
            if not os.path.exists(image_path):
                return {'success': False, 'message': 'Image file not found'}
            
            image = face_recognition.load_image_file(image_path)
            
            if image.size == 0:
                return {'success': False, 'message': 'Invalid or corrupt image file'}
            
            face_locations = face_recognition.face_locations(image, model="hog")
            
            if len(face_locations) == 0:
                return {'success': False, 'message': 'No face detected in the image. Please use a clear front-facing photo'}
            
            if len(face_locations) > 1:
                return {'success': False, 'message': 'Multiple faces detected. Please use an image with only one face'}
            
            face_encodings = face_recognition.face_encodings(image, face_locations, num_jitters=5)
            face_encoding = face_encodings[0]
            
            success = self.db_manager.add_employee(employee_id, name, email, department, face_encoding)
            
            if success:
                self.load_known_faces()
                try:
                    os.remove(image_path)
                except Exception as e:
                    app.logger.warning(f"Could not remove temporary image file: {str(e)}")
                return {'success': True, 'message': 'Employee added successfully'}
            else:
                return {'success': False, 'message': 'Employee ID already exists'}
                
        except Exception as e:
            return {'success': False, 'message': f'Error processing image: {str(e)}'}
    
    def recognize_faces(self, frame):
        scale = 1.0
        try:
            height, width = frame.shape[:2]
            if width > 1024:
                scale = 1024.0 / width
                process_frame = cv2.resize(frame, (1024, int(height * scale)))
            else:
                process_frame = frame
            
            rgb_frame = cv2.cvtColor(process_frame, cv2.COLOR_BGR2RGB)
            face_locations = face_recognition.face_locations(rgb_frame, model="hog")
            
            if not face_locations:
                return [], [], [], scale
            
            face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)
            face_names = []
            face_employee_ids = []
            
            for face_encoding in face_encodings:
                if not self.known_face_encodings:
                    face_names.append("Unknown")
                    face_employee_ids.append(None)
                    continue

                matches = face_recognition.compare_faces(self.known_face_encodings, face_encoding, tolerance=0.6)
                name = "Unknown"
                employee_id = None
                
                face_distances = face_recognition.face_distance(self.known_face_encodings, face_encoding)
                if len(face_distances) > 0:
                    best_match_index = np.argmin(face_distances)
                    if matches[best_match_index]:
                        name = self.known_face_names[best_match_index]
                        employee_id = self.known_employee_ids[best_match_index]
                
                face_names.append(name)
                face_employee_ids.append(employee_id)
            
            return face_locations, face_names, face_employee_ids, scale

        except Exception as e:
            app.logger.error(f"Error in face recognition: {str(e)}")
            return [], [], [], scale

# --- Flask Web Application ---
app = Flask(__name__)
app.secret_key = 'your-secret-key-change-in-production'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg'}

# Setup logging
if not app.debug:
    file_handler = RotatingFileHandler('app.log', maxBytes=10240, backupCount=10)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
    ))
    file_handler.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)
    app.logger.info('Face Recognition Attendance System startup')

from flask_moment import Moment
moment = Moment(app)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

face_system = FaceRecognitionSystem()

# --- Flask Routes ---
@app.route('/')
def dashboard():
    summary = face_system.db_manager.get_attendance_summary()
    recent_attendance = face_system.db_manager.get_attendance_records(days=3)
    return render_template('dashboard.html', summary=summary, recent_attendance=recent_attendance)

@app.route('/employees')
def employees():
    employees = face_system.db_manager.get_all_employees()
    return render_template('employees.html', employees=employees)

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']
@app.route('/delete_employee/<employee_id>', methods=['DELETE'])
def delete_employee(employee_id):
    # You should add more validation here in a real app, 
    # e.g., check if the user is an admin
    try:
        with face_system.db_manager.get_db_connection() as conn:
            # First, delete attendance records for the employee to maintain integrity
            conn.execute('DELETE FROM attendance WHERE employee_id = ?', (employee_id,))
            # Then, delete the employee
            cursor = conn.execute('DELETE FROM employees WHERE employee_id = ?', (employee_id,))
            conn.commit()
            
            if cursor.rowcount > 0:
                # Reload faces in memory after deletion
                face_system.load_known_faces()
                return jsonify({'success': True, 'message': 'Employee deleted successfully.'})
            else:
                return jsonify({'success': False, 'message': 'Employee not found.'}), 404

    except Exception as e:
        app.logger.error(f"Error deleting employee {employee_id}: {e}")
        return jsonify({'success': False, 'message': 'An error occurred on the server.'}), 500
@app.route('/add_employee', methods=['GET', 'POST'])
def add_employee():
    if request.method == 'POST':
        # --- START DEBUGGING ---
        print("\n--- ADD EMPLOYEE POST REQUEST RECEIVED ---")
        filepath = None
        try:
            print(f"Form data received: {request.form}")
            print(f"Files received: {request.files}")
            # --- END DEBUGGING ---

            employee_id = request.form.get('employee_id', '').strip()
            name = request.form.get('name', '').strip()
            email = request.form.get('email', '').strip()
            department = request.form.get('department', '').strip()

            if not all([employee_id, name, email, department]):
                flash('All fields are required.', 'error')
                print("DEBUG: Validation failed - Not all fields were filled.")
                return redirect(request.url)

            if not employee_id.isalnum():
                flash('Employee ID must contain only letters and numbers', 'error')
                print(f"DEBUG: Validation failed - Employee ID '{employee_id}' is not alphanumeric.")
                return redirect(request.url)
            
            if '@' not in email:
                flash('Please enter a valid email address', 'error')
                print(f"DEBUG: Validation failed - Email '{email}' is missing '@'.")
                return redirect(request.url)

            if 'photo' not in request.files or request.files['photo'].filename == '':
                flash('No photo selected', 'error')
                print("DEBUG: Validation failed - 'photo' not in request.files or filename is empty.")
                return redirect(request.url)
            
            photo = request.files['photo']

            if not allowed_file(photo.filename):
                flash('Invalid file type. Please upload a PNG, JPG, or JPEG image', 'error')
                print(f"DEBUG: Validation failed - File type for '{photo.filename}' is not allowed.")
                return redirect(request.url)
            
            filename = secure_filename(f"{employee_id}_{photo.filename}")
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            
            print(f"DEBUG: Attempting to save photo to: {filepath}")
            photo.save(filepath)
            print("DEBUG: Photo saved successfully.")
            
            print("DEBUG: Calling face_system.add_new_employee...")
            result = face_system.add_new_employee(employee_id, name, email, department, filepath)
            print(f"DEBUG: Result from face system: {result}")
            
            if result['success']:
                flash(result['message'], 'success')
                return redirect(url_for('employees'))
            else:
                flash(result['message'], 'error')
                if filepath and os.path.exists(filepath):
                    os.remove(filepath)
                return redirect(request.url)
                
        except Exception as e:
            app.logger.error(f"Error adding employee: {str(e)}")
            print(f"!!! CRITICAL ERROR in add_employee: {str(e)}") # DEBUG
            flash('An unexpected error occurred while processing your request.', 'error')
            if filepath and os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception as cleanup_error:
                    app.logger.error(f"Error cleaning up file after exception: {str(cleanup_error)}")
            return redirect(request.url)

    return render_template('add_employee.html')

@app.route('/attendance')
def attendance():
    records_from_db = face_system.db_manager.get_attendance_records(days=30)
    
    # Convert records to a list of dictionaries and calculate duration
    processed_records = []
    for record in records_from_db:
        record_dict = dict(record)
        duration_str = None
        if record_dict['check_in_time'] and record_dict['check_out_time']:
            check_in = record_dict['check_in_time']
            check_out = record_dict['check_out_time']
            duration = check_out - check_in
            
            hours, remainder = divmod(duration.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            duration_str = f"{hours}h {minutes}m"
            
        record_dict['duration'] = duration_str
        processed_records.append(record_dict)

    return render_template('attendance.html', records=processed_records)

@app.route('/mark_attendance', methods=['POST'])
def mark_attendance():
    data = request.json
    employee_id = data.get('employee_id')
    attendance_type = data.get('type', 'check_in')
    result = face_system.db_manager.mark_attendance(employee_id, attendance_type)
    return jsonify(result)

@app.route('/recognition')
def recognition():
    return render_template('recognition.html')

def gen_frames():
    try:
        if face_system.camera is None:
            face_system.camera = cv2.VideoCapture(0)
            if not face_system.camera.isOpened():
                raise RuntimeError("Could not open camera")
    except Exception as e:
        app.logger.error(f"Failed to initialize camera: {str(e)}")
        return
        
    while True:
        success, frame = face_system.camera.read()
        if not success:
            app.logger.error("Failed to capture frame from camera")
            break
        
        face_locations, face_names, face_employee_ids, scale = face_system.recognize_faces(frame)
        
        for (top, right, bottom, left), name in zip(face_locations, face_names):
            top = int(top / scale)
            right = int(right / scale)
            bottom = int(bottom / scale)
            left = int(left / scale)
            
            cv2.rectangle(frame, (left, top), (right, bottom), (0, 255, 0), 2)
            cv2.rectangle(frame, (left, top - 35), (right, top), (0, 255, 0), cv2.FILLED)
            cv2.putText(frame, name, (left + 6, top - 6), cv2.FONT_HERSHEY_DUPLEX, 0.8, (255, 255, 255), 1)
    
        ret, buffer = cv2.imencode('.jpg', frame)
        if ret:
            frame_bytes = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

@app.route('/video_feed')
def video_feed():
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    print("=" * 50)
    print("🚀 Face Recognition Attendance System")
    print("=" * 50)
    print("📊 Dashboard: http://localhost:5000")
    print("👥 Add Employees: http://localhost:5000/add_employee")
    print("📹 Recognition: http://localhost:5000/recognition")
    print("=" * 50)
    print("Press Ctrl+C to stop the server")
    print("=" * 50)
    
    

