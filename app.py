from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from sqlalchemy import text, func
from flask_socketio import SocketIO, join_room, leave_room, emit
import base64
import numpy as np
import cv2
from deepface import DeepFace
from collections import Counter
import datetime

# Inisialisasi Aplikasi
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///emotrack.db'
app.config['SECRET_KEY'] = 'ganti-dengan-kunci-rahasia-yang-sangat-aman'
db = SQLAlchemy(app)
socketio = SocketIO(app)

# --- Model Database ---
keanggotaan_kelas = db.Table('keanggotaan_kelas',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('kelas_id', db.Integer, db.ForeignKey('kelas.id'), primary_key=True),
    db.Column('status', db.String(20), default='pending')
)
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    nip_nisn = db.Column(db.String(50), unique=True, nullable=True)
    kelas_diikuti = db.relationship('Kelas', secondary=keanggotaan_kelas, lazy='subquery', backref=db.backref('siswa_terdaftar', lazy=True))
class Kelas(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nama_kelas = db.Column(db.String(100), nullable=False)
    guru_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    guru = db.relationship('User', backref=db.backref('kelas_dikelola', lazy=True))
class HasilEmosi(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    emotion = db.Column(db.String(50), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    kelas_id = db.Column(db.Integer, db.ForeignKey('kelas.id'), nullable=False)
    capture_group = db.Column(db.String(100), nullable=False) 
    user = db.relationship('User')
    kelas = db.relationship('Kelas')

# --- Fungsi Bantuan untuk Saran ---
def get_suggestion(dominant_emotion):
    suggestions = {
        'happy': "✅ Suasana kelas sangat positif! Pertahankan metode mengajar Anda. Ini adalah waktu yang baik untuk materi yang lebih menantang.",
        'neutral': "🤔 Sebagian besar siswa terlihat netral. Coba ajukan pertanyaan interaktif atau berikan studi kasus singkat untuk meningkatkan keterlibatan.",
        'sad': "😔 Banyak siswa terlihat sedih atau bosan. Pertimbangkan untuk memberikan jeda singkat, ice-breaking, atau mengubah metode penyampaian menjadi diskusi kelompok.",
        'angry': "😠 Ada indikasi frustrasi atau kebingungan. Coba perlambat tempo, ulangi konsep kunci, atau tanyakan langsung bagian mana yang sulit dipahami.",
        'fear': "😟 Siswa mungkin merasa cemas atau tertekan. Ciptakan suasana yang lebih mendukung dan yakinkan mereka bahwa tidak apa-apa untuk membuat kesalahan.",
        'surprise': "😮 Sesuatu yang baru atau tidak terduga terjadi. Manfaatkan momen ini untuk memulai diskusi atau menekankan poin penting dari materi.",
        'disgust': "🤢 Emosi ini jarang terjadi, bisa jadi ada gangguan eksternal atau materi yang sangat tidak menyenangkan. Periksa kondisi kelas secara langsung."
    }
    return suggestions.get(dominant_emotion, "Tidak ada saran spesifik untuk emosi ini.")

# --- Decorator & Route Autentikasi ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session: return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function
@app.route('/')
def index(): return redirect(url_for('login'))
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username, password, role, nip_nisn = request.form['username'], request.form['password'], request.form['role'], request.form['nip_nisn']
        hashed_password = generate_password_hash(password)
        new_user = User(username=username, password=hashed_password, role=role, nip_nisn=nip_nisn)
        db.session.add(new_user); db.session.commit(); return redirect(url_for('login'))
    return render_template('register.html')
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username, password = request.form['username'], request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            session['user_id'], session['username'], session['role'] = user.id, user.username, user.role
            return redirect(url_for('dashboard'))
        else: flash('Login gagal.')
    return render_template('login.html')
@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('login'))

# --- Route Dashboard & Kelas ---
@app.route('/dashboard')
@login_required
def dashboard():
    if session['role'] == 'guru': return redirect(url_for('dashboard_guru'))
    elif session['role'] == 'siswa': return redirect(url_for('dashboard_siswa'))
    return redirect(url_for('logout'))
@app.route('/dashboard_guru')
@login_required
def dashboard_guru():
    if session['role'] != 'guru': return redirect(url_for('dashboard'))
    permintaan_pending = db.session.query(User, Kelas).join(keanggotaan_kelas, (keanggotaan_kelas.c.user_id == User.id)).join(Kelas, (keanggotaan_kelas.c.kelas_id == Kelas.id)).filter(Kelas.guru_id == session['user_id']).filter(keanggotaan_kelas.c.status == 'pending').all()
    kelas_dikelola = Kelas.query.filter_by(guru_id=session['user_id']).all()
    return render_template('dashboard_guru.html', kelas_dikelola=kelas_dikelola, permintaan_pending=permintaan_pending)
@app.route('/dashboard_siswa')
@login_required
def dashboard_siswa():
    if session['role'] != 'siswa': return redirect(url_for('dashboard'))
    siswa = User.query.get(session['user_id'])
    status_keanggotaan = db.session.query(Kelas, keanggotaan_kelas.c.status.label('status')).join(keanggotaan_kelas).filter(keanggotaan_kelas.c.user_id == siswa.id).all()
    return render_template('dashboard_siswa.html', status_keanggotaan=status_keanggotaan)
@app.route('/daftar_kelas', methods=['GET', 'POST'])
@login_required
def daftar_kelas():
    if session['role'] != 'siswa': return redirect(url_for('dashboard'))
    siswa_id = session['user_id']
    if request.method == 'POST':
        kelas_id = request.form['kelas_id']
        kelas, siswa = Kelas.query.get(kelas_id), User.query.get(siswa_id)
        if kelas and siswa and kelas not in siswa.kelas_diikuti:
            siswa.kelas_diikuti.append(kelas); db.session.commit()
        return redirect(url_for('dashboard_siswa'))
    kelas_sudah_join_ids = [k.id for k in User.query.get(siswa_id).kelas_diikuti]
    semua_kelas = Kelas.query.filter(Kelas.id.notin_(kelas_sudah_join_ids)).all()
    return render_template('daftar_kelas.html', semua_kelas=semua_kelas)
@app.route('/buat_kelas', methods=['GET', 'POST'])
@login_required
def buat_kelas():
    if session['role'] != 'guru': return redirect(url_for('dashboard'))
    if request.method == 'POST':
        nama_kelas = request.form['nama_kelas']
        kelas_baru = Kelas(nama_kelas=nama_kelas, guru_id=session['user_id'])
        db.session.add(kelas_baru); db.session.commit()
        return redirect(url_for('buat_kelas'))
    return render_template('buat_kelas.html', kelas_dikelola=Kelas.query.filter_by(guru_id=session['user_id']).all())
@app.route('/approve', methods=['POST'])
@login_required
def approve_student():
    if session['role'] != 'guru': return redirect(url_for('dashboard'))
    user_id_to_approve, kelas_id_to_approve = request.form['user_id'], request.form['kelas_id']
    stmt = text("UPDATE keanggotaan_kelas SET status = 'approved' WHERE user_id = :user_id AND kelas_id = :kelas_id")
    db.session.execute(stmt, {'user_id': user_id_to_approve, 'kelas_id': kelas_id_to_approve})
    db.session.commit(); return redirect(url_for('dashboard_guru'))

# --- Route Kelas Live ---
@app.route('/live_class/<int:class_id>')
@login_required
def live_class_guru(class_id):
    if session['role'] != 'guru': return redirect(url_for('dashboard'))
    kelas = Kelas.query.get_or_404(class_id)
    if kelas.guru_id != session['user_id']: return redirect(url_for('dashboard_guru'))
    siswa_approved = User.query.join(keanggotaan_kelas).filter(keanggotaan_kelas.c.kelas_id == class_id, keanggotaan_kelas.c.status == 'approved').all()
    return render_template('live_class_guru.html', kelas=kelas, siswa_approved=siswa_approved)
@app.route('/classroom/<int:class_id>')
@login_required
def classroom_siswa(class_id):
    if session['role'] != 'siswa': return redirect(url_for('dashboard'))
    kelas = Kelas.query.get_or_404(class_id)
    teman_sekelas = User.query.join(keanggotaan_kelas).filter(keanggotaan_kelas.c.kelas_id == class_id, keanggotaan_kelas.c.status == 'approved').all()
    return render_template('classroom_siswa.html', kelas=kelas, teman_sekelas=teman_sekelas)

# --- ROUTE HASIL ANALISIS ---
@app.route('/hasil_analisis/<int:class_id>')
@login_required
def hasil_analisis(class_id):
    if session['role'] != 'guru': return redirect(url_for('dashboard'))
    kelas = Kelas.query.get_or_404(class_id)
    if kelas.guru_id != session['user_id']: return redirect(url_for('dashboard_guru'))
    hasil_grup = db.session.query(
        HasilEmosi.capture_group, 
        func.min(HasilEmosi.timestamp).label('timestamp'),
        func.group_concat(HasilEmosi.emotion).label('emotions')
    ).filter_by(kelas_id=class_id).group_by(HasilEmosi.capture_group).order_by(func.min(HasilEmosi.timestamp).desc()).all()
    laporan_sesi = []
    for grup in hasil_grup:
        emotions_list = grup.emotions.split(',') if grup.emotions else []
        total_siswa = len(emotions_list)
        if total_siswa > 0:
            counts = Counter(emotions_list)
            percentages = {emotion: int((count / total_siswa) * 100) for emotion, count in counts.items()}
            dominant_emotion = counts.most_common(1)[0][0]
            suggestion = get_suggestion(dominant_emotion)
            laporan_sesi.append({
                'timestamp': grup.timestamp,
                'percentages': percentages,
                'dominant_emotion': dominant_emotion,
                'suggestion': suggestion,
                'total_siswa': total_siswa
            })
        else:
             laporan_sesi.append({
                'timestamp': grup.timestamp,
                'percentages': {},
                'dominant_emotion': "N/A",
                'suggestion': "Tidak ada wajah yang terdeteksi pada sesi jepretan ini.",
                'total_siswa': 0
            })
    return render_template('hasil_analisis.html', kelas=kelas, laporan_sesi=laporan_sesi)

# --- Handler SocketIO ---
@socketio.on('join_teacher_room')
def handle_join_teacher_room(data):
    room = str(data['room']); join_room(room)
    print(f"Guru {session.get('username')} telah bergabung ke ruang kontrol: {room}")
@socketio.on('join_student_room')
def handle_join_student_room(data):
    room = str(data['room']); join_room(room)
    session['current_class_id'] = data['room']
    print(f"Siswa {session.get('username')} telah bergabung ke ruang kelas: {room}")
@socketio.on('teacher_command')
def handle_teacher_command(data):
    room, command = str(data['room']), data['command']
    print(f"Menerima perintah '{command}' dari guru untuk ruang {room}")
    if command == 'trigger_capture':
        capture_group_id = datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S%f')
        emit('capture_now', {'capture_group_id': capture_group_id}, to=room)
    else:
        emit('student_command', {'command': command}, to=room)
@socketio.on('video_frame')
def handle_video_frame(data):
    user_id = session.get('user_id')
    kelas_id = session.get('current_class_id')
    if not user_id or not kelas_id: return
    data_url = data['image']
    capture_group_id = data['capture_group_id']
    img_data = base64.b64decode(data_url.split(',')[1])
    nparr = np.frombuffer(img_data, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    try:
        analysis = DeepFace.analyze(img, actions=['emotion'], enforce_detection=False)
        dominant_emotion = analysis[0]['dominant_emotion']
        hasil_baru = HasilEmosi(emotion=dominant_emotion, user_id=user_id, kelas_id=kelas_id, capture_group=capture_group_id)
        db.session.add(hasil_baru)
        db.session.commit()
        print(f"Hasil disimpan: User {user_id}, Emosi {dominant_emotion}, Grup {capture_group_id}")
    except Exception as e:
        print(f"Error saat analisis: {e}")

if __name__ == '__main__':
    with app.app_context():
        db.create_all() 
    socketio.run(app, debug=True)