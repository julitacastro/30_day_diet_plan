import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from datetime import datetime, date
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-this-secret-key-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///jhuls_tracker.db')
# Render uses postgres:// but SQLAlchemy needs postgresql://
if app.config['SQLALCHEMY_DATABASE_URI'].startswith('postgres://'):
    app.config['SQLALCHEMY_DATABASE_URI'] = app.config['SQLALCHEMY_DATABASE_URI'].replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# Fix database connection dropping after Render free tier sleep
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 300,
}
# Keep sessions alive for 30 days
from datetime import timedelta
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=30)
app.config['SESSION_COOKIE_SECURE'] = False  # Must be False on Render free tier
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

@app.before_request
def make_session_permanent():
    from flask import session
    session.permanent = True

# =====================
# MODELS
# =====================

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    name = db.Column(db.String(100), default='Jhuls')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    logs = db.relationship('DailyLog', backref='user', lazy=True)
    progress = db.relationship('ProgressEntry', backref='user', lazy=True)
    smokes = db.relationship('CigaretteLog', backref='user', lazy=True)

class DailyLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    log_date = db.Column(db.Date, nullable=False)
    month_key = db.Column(db.String(10), nullable=False)  # e.g. "2026_05"
    day_index = db.Column(db.Integer, nullable=False)     # 0-29

    # Diet tracking
    water_cups = db.Column(db.Integer, default=0)
    coffee_cups = db.Column(db.Integer, default=0)
    breakfast_done = db.Column(db.Boolean, default=False)
    lunch_done = db.Column(db.Boolean, default=False)
    snack_done = db.Column(db.Boolean, default=False)
    dinner_done = db.Column(db.Boolean, default=False)

    # Exercise tracking
    exercise_done = db.Column(db.Boolean, default=False)
    exercise_notes = db.Column(db.Text, default='')
    exercise_felt = db.Column(db.String(20), default='')  # 'easy','good','hard','skipped'

    # Wellbeing
    energy_level = db.Column(db.Integer, default=0)   # 1-5
    mood = db.Column(db.Integer, default=0)            # 1-5
    sleep_hours = db.Column(db.Float, default=0)
    daily_note = db.Column(db.Text, default='')
    cigarettes_today = db.Column(db.Integer, default=0)

    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('user_id', 'month_key', 'day_index'),)

class ProgressEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    entry_date = db.Column(db.Date, nullable=False)

    # Measurements
    weight_kg = db.Column(db.Float)
    arm_cm = db.Column(db.Float)        # arm circumference
    waist_cm = db.Column(db.Float)
    hip_cm = db.Column(db.Float)
    calf_cm = db.Column(db.Float)

    # Fitness tests
    pushup_max = db.Column(db.Integer)
    plank_seconds = db.Column(db.Integer)
    calf_raise_max = db.Column(db.Integer)

    # Subjective
    eye_comfort = db.Column(db.Integer)   # 1-5 (how white/clear eyes feel)
    calf_pain = db.Column(db.Integer)     # 1-5 (1=lots of pain, 5=no pain)
    energy_general = db.Column(db.Integer) # 1-5
    gut_comfort = db.Column(db.Integer)   # 1-5 (bloating level, 5=no bloating)

    notes = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class CigaretteLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    logged_at = db.Column(db.DateTime, default=datetime.utcnow)
    log_date = db.Column(db.Date, nullable=False)
    trigger = db.Column(db.String(100), default='')   # 'thinking','after_meal','stress','habit','other'
    note = db.Column(db.Text, default='')             # forced reflection note
    craving_level = db.Column(db.Integer, default=3) # 1-5

class WaterReminder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    push_subscription = db.Column(db.Text, default='')  # JSON push subscription
    reminders_enabled = db.Column(db.Boolean, default=True)
    reminder_start_hour = db.Column(db.Integer, default=7)   # 7am
    reminder_end_hour = db.Column(db.Integer, default=21)    # 9pm
    interval_hours = db.Column(db.Integer, default=2)

@login_manager.user_loader
def load_user(user_id):
    try:
        return User.query.get(int(user_id))
    except Exception:
        return None

# =====================
# AUTH ROUTES
# =====================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user = User.query.filter_by(email=email).first()
        if user and bcrypt.check_password_hash(user.password, password):
            login_user(user, remember=True)
            return redirect(url_for('index'))
        flash('Invalid email or password.')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    # Only allow one registration — this is a personal app
    try:
        user_count = User.query.count()
    except Exception:
        db.session.rollback()
        user_count = 0

    if user_count > 0:
        flash('Registration is closed — this is a personal app.')
        return redirect(url_for('login'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        name = request.form.get('name', 'Jhuls').strip()
        password = request.form.get('password', '')

        # Double-check again right before saving (race condition guard)
        try:
            existing_count = User.query.count()
            existing_user = User.query.filter_by(email=email).first()
        except Exception:
            db.session.rollback()
            existing_count = 0
            existing_user = None

        if existing_count > 0:
            flash('Registration is closed — this is a personal app.')
            return redirect(url_for('login'))

        if existing_user:
            flash('Email already registered. Please sign in instead.')
            return redirect(url_for('login'))

        try:
            hashed = bcrypt.generate_password_hash(password).decode('utf-8')
            user = User(email=email, name=name, password=hashed)
            db.session.add(user)
            db.session.commit()
            login_user(user, remember=True)
            return redirect(url_for('index'))
        except Exception as e:
            db.session.rollback()
            flash('Something went wrong. Please try again.')
            return redirect(url_for('register'))

    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# =====================
# MAIN APP
# =====================

@app.route('/')
@login_required
def index():
    from data import MEAL_DATA, EXERCISE_DATA
    return render_template("index.html", user=current_user, meal_data=MEAL_DATA, exercise_data=EXERCISE_DATA)

@app.route('/progress')
@login_required
def progress_page():
    entries = ProgressEntry.query.filter_by(user_id=current_user.id).order_by(ProgressEntry.entry_date).all()
    return render_template('progress.html', user=current_user, entries=entries)

# =====================
# API ROUTES
# =====================

def get_or_create_log(user_id, month_key, day_index):
    log = DailyLog.query.filter_by(
        user_id=user_id, month_key=month_key, day_index=day_index
    ).first()
    if not log:
        # Parse date from month_key and day_index
        parts = month_key.split('_')
        y, m = int(parts[0]), int(parts[1])
        try:
            log_date = date(y, m, day_index + 1)
        except ValueError:
            log_date = date(y, m, 1)
        log = DailyLog(
            user_id=user_id,
            log_date=log_date,
            month_key=month_key,
            day_index=day_index
        )
        db.session.add(log)
        db.session.commit()
    return log

@app.route('/api/log/<month_key>/<int:day_index>', methods=['GET'])
@login_required
def get_log(month_key, day_index):
    log = DailyLog.query.filter_by(
        user_id=current_user.id, month_key=month_key, day_index=day_index
    ).first()
    if not log:
        return jsonify({})
    return jsonify({
        'water_cups': log.water_cups,
        'coffee_cups': log.coffee_cups,
        'breakfast_done': log.breakfast_done,
        'lunch_done': log.lunch_done,
        'snack_done': log.snack_done,
        'dinner_done': log.dinner_done,
        'exercise_done': log.exercise_done,
        'exercise_notes': log.exercise_notes,
        'exercise_felt': log.exercise_felt,
        'energy_level': log.energy_level,
        'mood': log.mood,
        'sleep_hours': log.sleep_hours,
        'daily_note': log.daily_note,
        'cigarettes_today': log.cigarettes_today,
    })

@app.route('/api/log/<month_key>/<int:day_index>', methods=['POST'])
@login_required
def save_log(month_key, day_index):
    log = get_or_create_log(current_user.id, month_key, day_index)
    data = request.get_json()
    for field in ['water_cups','coffee_cups','breakfast_done','lunch_done',
                  'snack_done','dinner_done','exercise_done','exercise_notes',
                  'exercise_felt','energy_level','mood','sleep_hours','daily_note',
                  'cigarettes_today']:
        if field in data:
            setattr(log, field, data[field])
    log.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/month-summary/<month_key>', methods=['GET'])
@login_required
def month_summary(month_key):
    logs = DailyLog.query.filter_by(user_id=current_user.id, month_key=month_key).all()
    summary = {}
    for log in logs:
        summary[log.day_index] = {
            'water_cups': log.water_cups,
            'coffee_cups': log.coffee_cups,
            'meals_done': sum([log.breakfast_done, log.lunch_done, log.snack_done, log.dinner_done]),
            'exercise_done': log.exercise_done,
            'energy_level': log.energy_level,
            'mood': log.mood,
        }
    return jsonify(summary)

@app.route('/api/progress', methods=['GET'])
@login_required
def get_progress():
    entries = ProgressEntry.query.filter_by(user_id=current_user.id).order_by(ProgressEntry.entry_date).all()
    return jsonify([{
        'id': e.id,
        'date': e.entry_date.isoformat(),
        'weight_kg': e.weight_kg,
        'arm_cm': e.arm_cm,
        'waist_cm': e.waist_cm,
        'hip_cm': e.hip_cm,
        'calf_cm': e.calf_cm,
        'pushup_max': e.pushup_max,
        'plank_seconds': e.plank_seconds,
        'calf_raise_max': e.calf_raise_max,
        'eye_comfort': e.eye_comfort,
        'calf_pain': e.calf_pain,
        'energy_general': e.energy_general,
        'gut_comfort': e.gut_comfort,
        'notes': e.notes,
    } for e in entries])

@app.route('/api/progress', methods=['POST'])
@login_required
def save_progress():
    data = request.get_json()
    entry_date = date.fromisoformat(data.get('date', date.today().isoformat()))
    entry = ProgressEntry(
        user_id=current_user.id,
        entry_date=entry_date,
        weight_kg=data.get('weight_kg'),
        arm_cm=data.get('arm_cm'),
        waist_cm=data.get('waist_cm'),
        hip_cm=data.get('hip_cm'),
        calf_cm=data.get('calf_cm'),
        pushup_max=data.get('pushup_max'),
        plank_seconds=data.get('plank_seconds'),
        calf_raise_max=data.get('calf_raise_max'),
        eye_comfort=data.get('eye_comfort'),
        calf_pain=data.get('calf_pain'),
        energy_general=data.get('energy_general'),
        gut_comfort=data.get('gut_comfort'),
        notes=data.get('notes', ''),
    )
    db.session.add(entry)
    db.session.commit()
    return jsonify({'ok': True, 'id': entry.id})

@app.route('/api/progress/<int:entry_id>', methods=['DELETE'])
@login_required
def delete_progress(entry_id):
    entry = ProgressEntry.query.filter_by(id=entry_id, user_id=current_user.id).first_or_404()
    db.session.delete(entry)
    db.session.commit()
    return jsonify({'ok': True})

# =====================
# CIGARETTE API
# =====================

@app.route('/api/smoke', methods=['POST'])
@login_required
def log_smoke():
    data = request.get_json()
    entry = CigaretteLog(
        user_id=current_user.id,
        log_date=date.today(),
        trigger=data.get('trigger', 'other'),
        note=data.get('note', ''),
        craving_level=data.get('craving_level', 3),
    )
    db.session.add(entry)
    # Also increment daily count
    from datetime import date as dt
    today = dt.today()
    month_key = f"{today.year}_{str(today.month).zfill(2)}"
    day_index = today.day - 1
    log = get_or_create_log(current_user.id, month_key, day_index)
    log.cigarettes_today = (log.cigarettes_today or 0) + 1
    db.session.commit()
    # Return shame message based on count
    count = log.cigarettes_today
    messages = {
        1: "1 cigarette today. You said you'd quit. There's still time to stop here.",
        2: "2 cigarettes. Remember why you started tracking this.",
        3: "3 today. This is the pattern you said you wanted to break.",
        4: "4 cigarettes. Your lungs, your eyes, your calves — they're all connected.",
        5: "5 today. You quit coffee. You can quit this too. But not if you don't try.",
    }
    msg = messages.get(count, f"{count} cigarettes today. Write it down. Feel it. Then decide tomorrow.")
    return jsonify({'ok': True, 'count': count, 'message': msg})

@app.route('/api/smoke/today', methods=['GET'])
@login_required
def get_smoke_today():
    today = date.today()
    logs = CigaretteLog.query.filter_by(
        user_id=current_user.id, log_date=today
    ).order_by(CigaretteLog.logged_at).all()
    return jsonify([{
        'id': e.id,
        'time': e.logged_at.strftime('%H:%M'),
        'trigger': e.trigger,
        'note': e.note,
        'craving_level': e.craving_level,
    } for e in logs])

@app.route('/api/smoke/history', methods=['GET'])
@login_required
def smoke_history():
    from sqlalchemy import func
    results = db.session.query(
        CigaretteLog.log_date,
        func.count(CigaretteLog.id).label('count')
    ).filter_by(user_id=current_user.id)     .group_by(CigaretteLog.log_date)     .order_by(CigaretteLog.log_date)     .all()
    return jsonify([{'date': str(r.log_date), 'count': r.count} for r in results])

@app.route('/api/smoke/<int:entry_id>', methods=['DELETE'])
@login_required
def delete_smoke(entry_id):
    entry = CigaretteLog.query.filter_by(id=entry_id, user_id=current_user.id).first_or_404()
    db.session.delete(entry)
    # Decrement daily count
    today = date.today()
    month_key = f"{today.year}_{str(today.month).zfill(2)}"
    log = DailyLog.query.filter_by(
        user_id=current_user.id, month_key=month_key, day_index=today.day - 1
    ).first()
    if log and log.cigarettes_today > 0:
        log.cigarettes_today -= 1
    db.session.commit()
    return jsonify({'ok': True})

# =====================
# INIT DB
# =====================
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=False)
