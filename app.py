import os
import json
import time
import uuid
import re
import base64
import secrets
import hmac
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import timedelta
import requests
from flask import Flask, render_template, request, redirect, url_for, session, g, Response, jsonify, send_from_directory, render_template_string
from werkzeug.utils import secure_filename
from upstash_redis import Redis

load_dotenv()
app = Flask(__name__)
# Set SECRET_KEY to a long random value in the hosting environment. The fallback is
# intentionally ephemeral so a hard-coded signing secret is never shipped.
app.secret_key = os.getenv('SECRET_KEY') or secrets.token_hex(32)

# الحساب الوحيد المسموح له بلوحة الإدارة
ADMIN_EMAIL = 'maleksameh015@gmail.com'

def is_admin_session():
    return session.get('admin_authenticated') is True and str(session.get('admin_email', '')).strip().lower() == ADMIN_EMAIL

# --- إعدادات رفع الملفات والفيديوهات (متوافقة مع Vercel) ---
IS_VERCEL = bool(os.environ.get('VERCEL'))
UPLOAD_FOLDER = '/tmp' if IS_VERCEL else os.path.join(app.root_path, 'static', 'uploads')
try:
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
except Exception:
    pass

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
# Upload size limit is configured below (50 MB).

# --- إعدادات الجلسة الدائمة ---
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=365)
app.config['SESSION_COOKIE_NAME'] = 'rewaq_session'
app.config['SESSION_REFRESH_EACH_REQUEST'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.getenv('SESSION_COOKIE_SECURE', 'true' if os.getenv('VERCEL') else 'false').lower() == 'true'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

# --- أرقام التواصل والروابط الرسمية الخاصة برِوَاق ---
DEVELOPER_WA = "201110388238"
SUPPORT_WA = "201200455707"
WA_COMMUNITY_URL = "https://chat.whatsapp.com/FqMM3Ts780S1epTrtCFxqt"

# جدول مواعيد المحاضرات
SCHEDULE_INFO = {
    "monday": "يوم الإثنين: فقه - عقيدة - سيرة نبوية",
    "thursday": "يوم الخميس: تفسير - حديث شريف - تزكية وأخلاق",
    "friday": "يوم الجمعة: اختبار شامل على دروس الأسبوع"
}

# --- إعدادات OneSignal للإشعارات ---
ONESIGNAL_APP_ID = os.getenv('ONESIGNAL_APP_ID', '')
ONESIGNAL_REST_API_KEY = os.getenv('ONESIGNAL_REST_API_KEY', '')

# --- الاتصال بقاعدة بيانات Upstash Redis الجديدة ---
UPSTASH_URL = os.getenv('UPSTASH_REDIS_REST_URL', '')
UPSTASH_TOKEN = os.getenv('UPSTASH_REDIS_REST_TOKEN', '')

redis = None
if UPSTASH_URL and UPSTASH_TOKEN:
    try:
        redis = Redis(url=UPSTASH_URL, token=UPSTASH_TOKEN)
        print("Upstash Redis client initialized for Rewaq.")
    except Exception as e:
        print("Redis connection error:", type(e).__name__)
else:
    print("INFO: Upstash Redis is not configured. Local JSON storage will be used for this computer.")

# Local fallback storage for running Rewaq on a personal computer.
# When Redis credentials are configured, Redis remains the primary database.
LOCAL_DATA_DIR = os.path.join(app.root_path, 'data')
LOCAL_DATA_FILE = os.path.join(LOCAL_DATA_DIR, 'rewaq_data.json')
try:
    os.makedirs(LOCAL_DATA_DIR, exist_ok=True)
except OSError as e:
    print("Could not create local data directory:", type(e).__name__)

# الأقسام الشرعية في منصة رِوَاق
ISLAMIC_SECTIONS = {
    'fiqh': {'name': 'فقه', 'icon': 'fa-scale-balanced', 'desc': 'أحكام العبادات والمعاملات على بصيرة'},
    'seerah': {'name': 'سيرة', 'icon': 'fa-book-quran', 'desc': 'سيرة النبي ﷺ العطرة وهديه القويم'},
    'tafseer': {'name': 'تفسير', 'icon': 'fa-quran', 'desc': 'تفسير وتدبر آيات كتاب الله العزيز'},
    'aqeedah': {'name': 'عقيدة', 'icon': 'fa-shield-halved', 'desc': 'أصول الإيمان وعقيدة أهل السنة والجماعة'},
    'tazkiyah': {'name': 'تزكية', 'icon': 'fa-heart', 'desc': 'أعمال القلوب وتهذيب النفوس والأخلاق'},
    'hadith': {'name': 'حديث', 'icon': 'fa-scroll', 'desc': 'دراسة وشرح أحاديث النبي ﷺ'},
    'catalog': {'name': 'كتالوج المنصة', 'icon': 'fa-compass', 'desc': 'دليل المنصة وخريطة طلب العلم الشرعي'}
}

TRACKS = {}

def load_data():
    default_data = {
        "users": [],
        "banned_users": [],
        "muted_users": [],
        "islamic_sections": {key: [] for key in ISLAMIC_SECTIONS.keys() if key != 'catalog'},
        "section_notes": {key: [] for key in ISLAMIC_SECTIONS.keys() if key != 'catalog'},
        "exams": {key: [] for key in ISLAMIC_SECTIONS.keys() if key != 'catalog'},
        "watch_records": {},
        "exam_results": {},
        "chat_messages": [],
        "chat_settings": {"is_locked": False, "locked_by": ""},
        "chat_moderators": [],
        "forum": [],
        "notes": {},
        "ad3yah": [],
        "azkar": {
            "sabah": [],
            "masea": [],
            "nom": [],
            "hadith": []
        },
        "push_subscriptions": []
    }
    
    if redis:
        try:
            raw = redis.get('rewaq_data')
            if raw:
                if isinstance(raw, str):
                    data = json.loads(raw)
                elif isinstance(raw, dict):
                    data = raw
                else:
                    data = json.loads(str(raw))

                if isinstance(data, dict):
                    data.setdefault('users', [])
                    data.setdefault('banned_users', [])
                    data.setdefault('muted_users', [])
                    
                    sec = data.setdefault('islamic_sections', {})
                    s_notes = data.setdefault('section_notes', {})
                    ex = data.setdefault('exams', {})
                    
                    for k in ISLAMIC_SECTIONS.keys():
                        if k != 'catalog':
                            sec.setdefault(k, [])
                            s_notes.setdefault(k, [])
                            ex.setdefault(k, [])

                    data.setdefault('watch_records', {})
                    data.setdefault('exam_results', {})
                    data.setdefault('chat_messages', [])
                    
                    chat_set = data.setdefault('chat_settings', {})
                    chat_set.setdefault('is_locked', False)
                    chat_set.setdefault('locked_by', '')

                    data.setdefault('chat_moderators', [])
                    data.setdefault('forum', [])
                    data.setdefault('notes', {})
                    data.setdefault('ad3yah', [])
                    
                    azk = data.setdefault('azkar', {})
                    for k in ['sabah', 'masea', 'nom', 'hadith']:
                        azk.setdefault(k, [])

                    data.setdefault('push_subscriptions', [])
                    # Migrate all legacy plaintext passwords immediately, without requiring
                    # each student to log in first. The entered password continues to work
                    # because authentication verifies the hash.
                    passwords_migrated = False
                    for user_record in data.get('users', []):
                        stored = str(user_record.get('password', ''))
                        if stored and not stored.startswith(('scrypt:', 'pbkdf2:')):
                            user_record['password'] = generate_password_hash(stored)
                            passwords_migrated = True
                    if passwords_migrated:
                        try:
                            redis.set('rewaq_data', json.dumps(data, ensure_ascii=False))
                        except Exception as e:
                            print('Password migration save error:', type(e).__name__)
                    return data
        except Exception as e:
            print("Redis load error:", type(e).__name__)

    # If Redis is not configured, read from a local JSON file so accounts survive
    # restarts during local development.
    if not redis:
        try:
            if os.path.isfile(LOCAL_DATA_FILE):
                with open(LOCAL_DATA_FILE, 'r', encoding='utf-8') as f:
                    local_data = json.load(f)
                if isinstance(local_data, dict):
                    for key, value in default_data.items():
                        local_data.setdefault(key, value)
                    return local_data
        except (OSError, json.JSONDecodeError) as e:
            print("Local data load error:", type(e).__name__)

    return default_data

def get_data(force_refresh=False):
    if 'data' not in g or force_refresh:
        g.data = load_data()
    return g.data

def save_data(data):
    saved_to_redis = False
    if redis:
        try:
            redis.set('rewaq_data', json.dumps(data, ensure_ascii=False))
            saved_to_redis = True
        except Exception as e:
            print("Redis save error:", type(e).__name__)

    # Persist locally when Redis is not configured. If Redis is configured but
    # temporarily unavailable, also keep a local emergency copy for this computer.
    if not saved_to_redis:
        try:
            os.makedirs(LOCAL_DATA_DIR, exist_ok=True)
            temp_file = LOCAL_DATA_FILE + '.tmp'
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(temp_file, LOCAL_DATA_FILE)
        except OSError as e:
            print("Local data save error:", type(e).__name__)
    g.data = data

def get_current_user(data):
    user_identifier = session.get('user_identifier')
    if user_identifier:
        for u in data.get('users', []):
            if str(u.get('identifier')).strip() == str(user_identifier).strip():
                return u
    user_name = session.get('user')
    if user_name:
        for u in data.get('users', []):
            if u.get('name') == user_name:
                return u
    return None

def is_user_moderator(user_identifier, data):
    if not user_identifier and not session.get('logged_in'):
        return False
    uid = str(user_identifier).strip() if user_identifier else ''
    mods = [str(m).strip() for m in data.get('chat_moderators', []) if str(m).strip()]
    is_admin = is_admin_session()
    return (uid in mods) or is_admin

def notify_all(title, body, target_url="/"):
    try:
        data = get_data()
        data.setdefault('pending_notifications', []).append({
            'title': title,
            'body': body,
            'url': target_url,
            'timestamp': time.time()
        })
        save_data(data)
    except Exception as e:
        print("Notification save error:", e)

    if ONESIGNAL_APP_ID and ONESIGNAL_REST_API_KEY:
        try:
            api_url = "https://onesignal.com/api/v1/notifications"
            api_key = str(ONESIGNAL_REST_API_KEY).strip()
            auth_value = f"Key {api_key}" if api_key.startswith("os_v2_") else f"Basic {api_key}"
            headers = {
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": auth_value
            }
            final_url = target_url
            try:
                if target_url and target_url.startswith('/'):
                    final_url = request.url_root.rstrip('/') + target_url
            except Exception:
                final_url = target_url

            payload = {
                "app_id": str(ONESIGNAL_APP_ID).strip(),
                "included_segments": ["Total Subscriptions"],
                "headings": {"en": title, "ar": title},
                "contents": {"en": body, "ar": body},
                "url": final_url
            }
            requests.post(api_url, headers=headers, json=payload, timeout=5)
        except Exception as e:
            print("OneSignal send error:", e)

@app.before_request
def check_session_and_ban():
    session.permanent = True
    if 'user_identifier' in session:
        data = get_data()
        banned = [str(b).strip() for b in data.get('banned_users', [])]
        if str(session['user_identifier']).strip() in banned:
            session.clear()
            return "<div style='direction:rtl; text-align:center; padding:50px; font-family:sans-serif;'><h2>⚠️ تم حظر حسابك من دخول المنصة نهائياً لمخالفة التعليمات!</h2></div>", 403
        # Invalidate sessions for student accounts that have been deleted from the database.
        if session.get('user') and not get_current_user(data):
            session.clear()
            return redirect(url_for('login'))

@app.context_processor
def inject_globals():
    data = get_data()
    user_note = ""
    current_user = get_current_user(data)
    is_mod = False
    
    if current_user:
        user_key = str(current_user.get('identifier') or current_user.get('name', ''))
        user_note = data.get('notes', {}).get(user_key, "")
        is_mod = is_user_moderator(current_user.get('identifier'), data)
    elif is_admin_session():
        is_mod = True

    return {
        'platform_name': 'رِوَاق',
        'developer_wa': DEVELOPER_WA,
        'developer_wa_link': f"https://wa.me/{DEVELOPER_WA}",
        'support_wa': SUPPORT_WA,
        'support_wa_link': f"https://wa.me/{SUPPORT_WA}",
        'wa_community_url': WA_COMMUNITY_URL,
        'user_note': user_note,
        'current_user': current_user,
        'user': current_user,
        'is_moderator': is_mod,
        'ISLAMIC_SECTIONS': ISLAMIC_SECTIONS,
        'SCHEDULE_INFO': SCHEDULE_INFO,
        'tracks': TRACKS
    }

# --- مسارات الحسابات والتسجيل ---

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user' in session:
        return redirect(url_for('index'))
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        identifier = request.form.get('identifier', '').strip()
        password = request.form.get('password', '').strip()
        
        if not re.match(r'^[\u0600-\u06FF\s]+$', name):
            return "<div style='direction:rtl;text-align:center;padding:50px;font-family:sans-serif;'><h3>⚠️ خطأ: يجب كتابة الاسم باللغة العربية فقط!</h3><a href='/register'>العودة للتسجيل</a></div>"
            
        if not re.match(r'^[0-9]+$', identifier):
            return "<div style='direction:rtl;text-align:center;padding:50px;font-family:sans-serif;'><h3>⚠️ خطأ: يجب إدخال رقم الهاتف بالأرقام الإنجليزية (0-9) فقط بدون مسافات أو حروف!</h3><a href='/register'>العودة للتسجيل</a></div>"
        if len(password) < 8:
            return "<div style='direction:rtl;text-align:center;padding:50px;font-family:sans-serif;'><h3>⚠️ كلمة المرور يجب ألا تقل عن 8 أحرف.</h3><a href='/register'>العودة للتسجيل</a></div>"
        
        if identifier and password:
            data = get_data(force_refresh=True)
            banned = [str(b).strip() for b in data.get('banned_users', [])]
            if identifier in banned:
                return "<div style='direction:rtl;text-align:center;padding:50px;font-family:sans-serif;'><h3>⚠️ هذا الرقم محظور من التسجيل نهائياً!</h3></div>"

            for u in data.get('users', []):
                if str(u.get('identifier')).strip() == identifier:
                    return "الحساب مسجل بالفعل! <a href='/login'>سجل دخولك من هنا</a>"
            
            user_obj = {
                'name': name,
                'identifier': identifier, 
                'password': generate_password_hash(password)
            }
            data.setdefault('users', []).append(user_obj)
            save_data(data)
            
            session.clear()
            session.permanent = True
            session['user'] = user_obj['name']
            session['user_identifier'] = identifier
            session['show_schedule_popup'] = True
            return redirect(url_for('index'))
            
    return render_template(
        'register.html',
        tracks=TRACKS,
        first_time=request.args.get('first_time') == '1',
        prefill_identifier=request.args.get('identifier', '').strip()
    )

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user' in session:
        return redirect(url_for('index'))
    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        password = request.form.get('password', '').strip()

        # دخول المدير من نفس صفحة الدخول باستخدام البريد المحدد وكلمة مرور الإدارة.
        # لا يُمنح أي حساب آخر صلاحية الإدارة حتى لو كتب البريد في خانة الدخول.
        if identifier.lower() == ADMIN_EMAIL:
            admin_password = os.getenv('ADMIN_PASSWORD', '')
            if admin_password and hmac.compare_digest(password, admin_password):
                session.clear()
                session.permanent = True
                session['logged_in'] = True
                session['admin_authenticated'] = True
                session['admin_email'] = ADMIN_EMAIL
                return redirect(url_for('admin'))
            return render_template('login.html', error='بيانات دخول المدير غير صحيحة.', prefill_identifier=identifier), 401
        
        data = get_data(force_refresh=True)
        banned = [str(b).strip() for b in data.get('banned_users', [])]
        if identifier in banned:
            return "<div style='direction:rtl;text-align:center;padding:50px;font-family:sans-serif;'><h3>⚠️ تم حظر هذا الحساب من دخول المنصة نهائياً!</h3></div>"

        matched_user = None
        for u in data.get('users', []):
            if str(u.get('identifier')).strip() == identifier:
                matched_user = u
                break

        # رقم الهاتف غير مسجل: وجّه الطالب مباشرة إلى إنشاء حساب، مع الاحتفاظ بالرقم.
        if matched_user is None:
            return redirect(url_for('register', first_time='1', identifier=identifier))

        stored_password = str(matched_user.get('password', ''))
        password_ok = False
        if stored_password.startswith(('scrypt:', 'pbkdf2:')):
            try:
                password_ok = check_password_hash(stored_password, password)
            except (ValueError, TypeError):
                password_ok = False
        else:
            # ترقية كلمات المرور القديمة إلى تجزئة آمنة بعد نجاح تسجيل الدخول.
            password_ok = hmac.compare_digest(stored_password, password)
            if password_ok:
                matched_user['password'] = generate_password_hash(password)
                save_data(data)

        if password_ok:
            session.clear()
            session.permanent = True
            session['user'] = matched_user.get('name', identifier)
            session['user_identifier'] = matched_user.get('identifier')
            session['show_schedule_popup'] = True
            return redirect(url_for('index'))

        return render_template('login.html', error='كلمة المرور غير صحيحة. جرّب مرة أخرى أو استخدم رقم الهاتف المسجل لإنشاء حساب إذا لم تكن سجلت من قبل.', prefill_identifier=identifier), 401
        
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if 'user' not in session:
        return redirect(url_for('login'))
    data = get_data(force_refresh=True)
    current_user = get_current_user(data)
    user_index = -1
    if current_user:
        for idx, u in enumerate(data.get('users', [])):
            if u.get('identifier') == current_user.get('identifier'):
                user_index = idx
                break

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        password = request.form.get('password', '').strip()
        if current_user and user_index != -1:
            if name and re.match(r'^[\u0600-\u06FF\s]+$', name):
                data['users'][user_index]['name'] = name
                session['user'] = name
            if password:
                if len(password) < 8:
                    return render_template('settings.html', user=current_user, tracks=TRACKS, error='كلمة المرور يجب ألا تقل عن 8 أحرف.')
                data['users'][user_index]['password'] = generate_password_hash(password)
            save_data(data)
            return redirect(url_for('index'))

    return render_template('settings.html', user=current_user, tracks=TRACKS)

# --- الصفحة الرئيسية ---

@app.route('/')
def index():
    if 'user' not in session:
        return redirect(url_for('login'))
    data = get_data()
    current_user = get_current_user(data)
    show_popup = session.pop('show_schedule_popup', False)
    user_id = str(current_user.get('identifier')) if current_user else ''
    lectures = [lecture for items in data.get('islamic_sections', {}).values() for lecture in items]
    watch_records = data.get('watch_records', {})
    watched_count = sum(1 for lecture in lectures if user_id and user_id in watch_records.get(str(lecture.get('id')), []))
    total_lectures = len(lectures)
    progress_percent = round((watched_count / total_lectures) * 100) if total_lectures else 0
    return render_template('index.html', user=current_user, sections=ISLAMIC_SECTIONS,
                           show_schedule_popup=show_popup, watched_count=watched_count,
                           total_lectures=total_lectures, progress_percent=progress_percent)

@app.route('/catalog')
def catalog():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('catalog.html')

# --- تفاصيل القسم الشرعي (المحاضرات + المذكرات + الامتحانات) ---

@app.route('/section/<sec_key>')
def section_detail(sec_key):
    if 'user' not in session:
        return redirect(url_for('login'))
    if sec_key == 'catalog':
        return redirect(url_for('catalog'))
    if sec_key not in ISLAMIC_SECTIONS:
        return redirect(url_for('index'))
    
    data = get_data()
    current_user = get_current_user(data)
    user_id = str(current_user.get('identifier')) if current_user else ''
    
    lectures = data.get('islamic_sections', {}).get(sec_key, [])
    notes = data.get('section_notes', {}).get(sec_key, [])
    exams = data.get('exams', {}).get(sec_key, [])
    watch_records = data.get('watch_records', {})
    
    for lec in lectures:
        lec_id = lec.get('id')
        lec['is_watched'] = user_id in watch_records.get(lec_id, [])

    return render_template('section_detail.html', 
                           section_key=sec_key, 
                           section_info=ISLAMIC_SECTIONS[sec_key], 
                           lectures=lectures,
                           notes=notes,
                           exams=exams)

# زرار تسجيل المشاهدة
@app.route('/api/mark_watched', methods=['POST'])
def mark_watched():
    if 'user' not in session:
        return jsonify({'success': False, 'message': 'يرجى تسجيل الدخول أولاً'}), 401
    
    payload = request.get_json(silent=True) or {}
    lecture_id = payload.get('lecture_id')
    
    if not lecture_id:
        return jsonify({'success': False, 'message': 'معرف المحاضرة غير صحيح'}), 400
        
    data = get_data(force_refresh=True)
    current_user = get_current_user(data)
    user_id = str(current_user.get('identifier'))
    
    watch_records = data.setdefault('watch_records', {})
    watchers = watch_records.setdefault(lecture_id, [])
    
    if user_id not in watchers:
        watchers.append(user_id)
        save_data(data)
        return jsonify({'success': True, 'watched': True, 'message': 'تم تسجيل إتمام المشاهدة بنجاح!'})
    else:
        return jsonify({'success': True, 'watched': True, 'message': 'المشاهدة مسجلة مسبقاً'})

# تقديم الامتحان
@app.route('/exam/<sec_key>/<exam_id>', methods=['GET', 'POST'])
def take_exam(sec_key, exam_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    
    data = get_data(force_refresh=True)
    exams_list = data.get('exams', {}).get(sec_key, [])
    exam = next((e for e in exams_list if e.get('id') == exam_id), None)
    
    if not exam:
        return "الامتحان غير موجود!", 404
    
    if exam.get('external_link'):
        return redirect(exam['external_link'])
        
    current_user = get_current_user(data)
    user_id = str(current_user.get('identifier'))
    
    if request.method == 'POST':
        questions = exam.get('questions', [])
        score = 0
        total = len(questions)
        
        for idx, q in enumerate(questions):
            ans = request.form.get(f'question_{idx}')
            if ans and str(ans).strip() == str(q.get('correct_answer')).strip():
                score += 1
                
        exam_results = data.setdefault('exam_results', {})
        results_list = exam_results.setdefault(exam_id, [])
        results_list.append({
            'user_identifier': user_id,
            'user_name': current_user.get('name'),
            'score': score,
            'total': total,
            'date': time.strftime("%Y-%m-%d %H:%M")
        })
        save_data(data)
        
        result_html = """
        <!DOCTYPE html>
        <html lang="ar" dir="rtl">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>نتيجة الاختبار</title>
            <style>
                body { background:#070d18; color:#fff; font-family:'Segoe UI', Tahoma; padding:20px; text-align:center; }
                .box { max-width:480px; margin:40px auto; background:#0f172a; padding:30px 20px; border-radius:16px; border:1px solid #10b981; }
                .score { font-size:36px; font-weight:bold; color:#fde047; margin:15px 0; }
                .btn { display:inline-block; margin-top:20px; padding:12px 25px; background:#10b981; color:#fff; text-decoration:none; border-radius:10px; font-weight:bold; }
            </style>
        </head>
        <body>
            <div class="box">
                <h2 style="color:#10b981;">تم إرسال إجاباتك بنجاح! 🎉</h2>
                <p style="color:#94a3b8; margin-top:8px;">{{ exam.get('title') }}</p>
                <div class="score">{{ score }} / {{ total }}</div>
                <a href="/section/{{ section_key }}" class="btn">العودة للقسم</a>
            </div>
        </body>
        </html>
        """
        return render_template_string(result_html, score=score, total=total, exam=exam, section_key=sec_key)
        
    exam_html = """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{{ exam.get('title') }}</title>
        <style>
            body { background:#070d18; color:#fff; font-family:'Segoe UI', Tahoma; padding:15px; }
            .box { max-width:550px; margin:0 auto; background:#0f172a; padding:20px; border-radius:14px; border:1px solid #1e293b; }
            .q { background:#111e36; padding:15px; border-radius:10px; margin-bottom:15px; border:1px solid #1e293b; }
            .opt { margin:8px 0; display:block; cursor:pointer; }
            .btn { width:100%; padding:12px; background:#10b981; color:#fff; border:none; border-radius:10px; font-weight:bold; font-size:15px; cursor:pointer; }
        </style>
    </head>
    <body>
        <div class="box">
            <h2 style="color:#f59e0b; margin-bottom:15px; text-align:center;">✍️ {{ exam.get('title') }}</h2>
            <form method="POST">
                {% for q in exam.get('questions', []) %}
                    {% set q_idx = loop.index0 %}
                    <div class="q">
                        <p style="font-weight:bold; margin-bottom:10px;">{{ loop.index }}. {{ q.get('question') }}</p>
                        {% for opt in q.get('options', []) %}
                            <label class="opt">
                                <input type="radio" name="question_{{ q_idx }}" value="{{ loop.index }}" required>
                                {{ opt }}
                            </label>
                        {% endfor %}
                    </div>
                {% endfor %}
                <button type="submit" class="btn">إرسال الإجابات وإنهاء الاختبار</button>
            </form>
        </div>
    </body>
    </html>
    """
    return render_template_string(exam_html, exam=exam, section_key=sec_key)

# --- نظام الشات التفاعلي والبديل لقسم مهامك ---

@app.route('/chat')
@app.route('/tasks')
def chat():
    if 'user' not in session:
        return redirect(url_for('login'))
    data = get_data()
    current_user = get_current_user(data)
    user_id = str(current_user.get('identifier')).strip() if current_user else ''
    is_mod = is_user_moderator(user_id, data)
    
    chat_settings = data.get('chat_settings', {'is_locked': False, 'locked_by': ''})
    messages = data.get('chat_messages', [])[-100:]
    muted_users = [str(m).strip() for m in data.get('muted_users', [])]
    moderators = [str(m).strip() for m in data.get('chat_moderators', [])]
    
    all_users = data.get('users', []) if is_mod else []
    
    return render_template('tasks.html', 
                           messages=messages, 
                           chat_settings=chat_settings, 
                           is_moderator=is_mod,
                           all_users=all_users,
                           muted_users=muted_users,
                           moderators=moderators)

# رفع الصور في الشات وتحويلها إلى Base64 Data URI
@app.route('/api/chat/upload_image', methods=['POST'])
def chat_upload_image():
    if 'user' not in session:
        return jsonify({'success': False, 'message': 'يرجى تسجيل الدخول أولاً'}), 401
    
    data = get_data()
    current_user = get_current_user(data)
    user_id = str(current_user.get('identifier')).strip() if current_user else ''
    muted_users = [str(m).strip() for m in data.get('muted_users', [])]
    if user_id in muted_users:
        return jsonify({'success': False, 'message': 'تم كتم حسابك!'}), 403

    img_file = request.files.get('image')
    if not img_file or not img_file.filename:
        return jsonify({'success': False, 'message': 'لم يتم اختيار صورة'}), 400

    file_bytes = img_file.read()
    if len(file_bytes) > 2 * 1024 * 1024:
        return jsonify({'success': False, 'message': 'حجم الصورة كبير جداً (الحد الأقصى 2MB)'}), 400
    
    mime_type = img_file.content_type or 'image/jpeg'
    b64_str = base64.b64encode(file_bytes).decode('utf-8')
    data_uri = f"data:{mime_type};base64,{b64_str}"
    return jsonify({'success': True, 'image_url': data_uri})

@app.route('/api/chat/send', methods=['POST'])
def chat_send():
    if 'user' not in session:
        return jsonify({'success': False, 'message': 'سجل الدخول أولاً'}), 401
        
    data = get_data(force_refresh=True)
    current_user = get_current_user(data)
    user_id = str(current_user.get('identifier')).strip() if current_user else ''
    is_mod = is_user_moderator(user_id, data)
    
    muted_users = [str(m).strip() for m in data.get('muted_users', [])]
    if user_id in muted_users:
        return jsonify({'success': False, 'message': 'تم كتم حسابك من قِبل المشرفين!'}), 403
        
    chat_settings = data.get('chat_settings', {'is_locked': False, 'locked_by': ''})
    if chat_settings.get('is_locked') and not is_mod:
        locker = chat_settings.get('locked_by') or 'أحد المشرفين'
        return jsonify({'success': False, 'message': f'عذراً، الشات مغلق حالياً بواسطة المشرف ({locker}).'}), 403
        
    payload = request.get_json(silent=True) or {}
    text = payload.get('text', '').strip()
    image_url = payload.get('image_url', '').strip()
    mentions = payload.get('mentions', [])
    
    if text or image_url:
        msg_id = str(uuid.uuid4())[:8]
        msg = {
            'id': msg_id,
            'user_name': current_user.get('name') if current_user else 'طالب علم',
            'user_identifier': user_id,
            'is_admin': is_admin_session(),
            'is_mod': is_mod,
            'text': text,
            'image_url': image_url,
            'reactions': {},
            'mentions': mentions,
            'time': time.strftime("%I:%M %p")
        }
        data.setdefault('chat_messages', []).append(msg)
        if len(data['chat_messages']) > 200:
            data['chat_messages'] = data['chat_messages'][-200:]
        save_data(data)
        return jsonify({'success': True, 'message': msg})
        
    return jsonify({'success': False, 'message': 'الرسالة فارغة'}), 400

# التفاعل مع الرسائل بالإيموجي
@app.route('/api/chat/react', methods=['POST'])
def chat_react():
    if 'user' not in session:
        return jsonify({'success': False, 'message': 'يرجى تسجيل الدخول أولاً'}), 401
        
    payload = request.get_json(silent=True) or {}
    msg_id = payload.get('msg_id')
    emoji = payload.get('emoji', '').strip()
    
    if not msg_id or not emoji:
        return jsonify({'success': False, 'message': 'بيانات غير مكتملة'}), 400

    data = get_data(force_refresh=True)
    current_user = get_current_user(data)
    user_id = str(current_user.get('identifier')).strip() if current_user else ''

    messages = data.get('chat_messages', [])
    target_msg = next((m for m in messages if m.get('id') == msg_id), None)
    if not target_msg:
        return jsonify({'success': False, 'message': 'الرسالة غير موجودة'}), 404

    reactions = target_msg.setdefault('reactions', {})
    users_list = reactions.setdefault(emoji, [])
    if user_id in users_list:
        users_list.remove(user_id)
        if not users_list:
            reactions.pop(emoji, None)
    else:
        users_list.append(user_id)

    save_data(data)
    return jsonify({'success': True, 'reactions': reactions})

@app.route('/api/chat/messages')
def get_chat_messages():
    if 'user' not in session:
        return jsonify([]), 401
    data = get_data()
    chat_settings = data.get('chat_settings', {'is_locked': False, 'locked_by': ''})
    return jsonify({
        'messages': data.get('chat_messages', [])[-100:],
        'is_locked': chat_settings.get('is_locked', False),
        'locked_by': chat_settings.get('locked_by', '')
    })

@app.route('/chat/toggle_lock', methods=['POST'])
def chat_toggle_lock():
    if 'user' not in session:
        return redirect(url_for('login'))
    data = get_data(force_refresh=True)
    current_user = get_current_user(data)
    user_id = str(current_user.get('identifier')).strip() if current_user else ''
    if not is_user_moderator(user_id, data):
        return "غير مصرح لك!", 403
        
    settings = data.setdefault('chat_settings', {'is_locked': False, 'locked_by': ''})
    new_locked = not settings.get('is_locked', False)
    settings['is_locked'] = new_locked
    
    mod_name = current_user.get('name') if current_user else 'الإدارة'
    
    if new_locked:
        settings['locked_by'] = mod_name
        system_msg = {
            'id': str(uuid.uuid4())[:8],
            'user_name': 'تنبيه إداري 📢',
            'user_identifier': 'system',
            'is_admin': True,
            'is_mod': True,
            'text': f'🔒 قام المشرف ({mod_name}) بقفل الشات مؤقتاً (الكتابة للمشرفين فقط).',
            'image_url': '',
            'reactions': {},
            'mentions': [],
            'time': time.strftime("%I:%M %p")
        }
        data.setdefault('chat_messages', []).append(system_msg)
    else:
        settings['locked_by'] = ''
        system_msg = {
            'id': str(uuid.uuid4())[:8],
            'user_name': 'تنبيه إداري 📢',
            'user_identifier': 'system',
            'is_admin': True,
            'is_mod': True,
            'text': f'🔓 قام المشرف ({mod_name}) بفتح الشات للجميع، بارك الله فيكم.',
            'image_url': '',
            'reactions': {},
            'mentions': [],
            'time': time.strftime("%I:%M %p")
        }
        data.setdefault('chat_messages', []).append(system_msg)

    save_data(data)
    return redirect(url_for('chat'))

@app.route('/chat/toggle_mute/<user_identifier>', methods=['POST'])
def chat_toggle_mute(user_identifier):
    if 'user' not in session:
        return redirect(url_for('login'))
    data = get_data(force_refresh=True)
    current_user = get_current_user(data)
    user_id = str(current_user.get('identifier')).strip() if current_user else ''
    if not is_user_moderator(user_id, data):
        return "غير مصرح لك!", 403
        
    muted = data.setdefault('muted_users', [])
    u_id = str(user_identifier).strip()
    if u_id in muted:
        muted.remove(u_id)
    else:
        muted.append(u_id)
    save_data(data)
    return redirect(url_for('chat'))

@app.route('/chat/clear', methods=['POST'])
def chat_clear():
    if 'user' not in session:
        return redirect(url_for('login'))
    data = get_data(force_refresh=True)
    current_user = get_current_user(data)
    user_id = str(current_user.get('identifier')).strip() if current_user else ''
    if not is_user_moderator(user_id, data):
        return "غير مصرح لك!", 403
    data['chat_messages'] = []
    save_data(data)
    return redirect(url_for('chat'))

@app.route('/chat/delete_message/<msg_id>', methods=['POST'])
@app.route('/api/chat/delete/<msg_id>', methods=['POST'])
def chat_delete_msg(msg_id):
    if 'user' not in session:
        if request.path.startswith('/api/'):
            return jsonify({'success': False, 'message': 'غير مسجل'}), 401
        return redirect(url_for('login'))
        
    data = get_data(force_refresh=True)
    current_user = get_current_user(data)
    user_id = str(current_user.get('identifier')).strip() if current_user else ''
    if not is_user_moderator(user_id, data):
        if request.path.startswith('/api/'):
            return jsonify({'success': False, 'message': 'غير مصرح'}), 403
        return "غير مصرح لك!", 403
        
    messages = data.get('chat_messages', [])
    data['chat_messages'] = [m for m in messages if m.get('id') != msg_id]
    save_data(data)
    if request.path.startswith('/api/'):
        return jsonify({'success': True})
    return redirect(url_for('chat'))

# --- قسم حسانات ---

@app.route('/hasanat')
def hasanat():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('hasanat.html')

@app.route('/hasanat/quran_surahs')
def quran_surahs():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('quran_surahs.html')

@app.route('/hasanat/quran_juz')
def quran_juz():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('quran_juz.html')

@app.route('/hasanat/ad3yah')
def ad3yah():
    if 'user' not in session:
        return redirect(url_for('login'))
    data = get_data()
    return render_template('ad3yah.html', ad3yah=data.get('ad3yah', []))

@app.route('/hasanat/azkar_hadith')
def azkar_hadith_menu():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('azkar_hadith_menu.html')

@app.route('/hasanat/azkar/<category>')
def azkar_category(category):
    if 'user' not in session:
        return redirect(url_for('login'))
    cat_titles = {'sabah': 'أذكار الصباح', 'masea': 'أذكار المساء', 'nom': 'أذكار النوم', 'hadith': 'الأربعين النووية'}
    title = cat_titles.get(category, 'الأذكار')
    data = get_data()
    azkar_list = data.get('azkar', {}).get(category, [])
    return render_template('azkar_detail.html', category=category, title=title, azkar=azkar_list)

@app.route('/hasanat/sebha')
def sebha():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('sebha.html')

# --- مسارات الأيقونة وملف Manifest والـ Service Worker ---

@app.route('/app-icon.png')
def serve_app_icon():
    static_dir = os.path.join(app.root_path, 'static')
    logo_path = os.path.join(static_dir, 'logo.png')
    if os.path.exists(logo_path):
        return send_from_directory(static_dir, 'logo.png', mimetype='image/png')
    
    svg_icon = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">
        <rect width="512" height="512" rx="120" fill="#070d18"/>
        <circle cx="256" cy="256" r="210" fill="#0f172a" stroke="#10b981" stroke-width="16"/>
        <text x="50%" y="54%" text-anchor="middle" fill="#f59e0b" font-size="130" font-weight="bold" font-family="sans-serif" dy=".3em">رِوَاق</text>
    </svg>"""
    return Response(svg_icon, mimetype='image/svg+xml')

@app.route('/manifest.json')
def manifest():
    manifest_data = {
        "id": "/",
        "name": "رِوَاق للعلوم الشرعية",
        "short_name": "رِوَاق",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "dir": "rtl",
        "lang": "ar",
        "background_color": "#070d18",
        "theme_color": "#10b981",
        "categories": ["education", "books"],
        "icons": [
            {"src": "/app-icon.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/app-icon.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/app-icon.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}
        ]
    }
    return Response(
        json.dumps(manifest_data, ensure_ascii=False),
        mimetype='application/manifest+json',
        headers={'Access-Control-Allow-Origin': '*', 'Content-Type': 'application/manifest+json; charset=utf-8'}
    )

@app.route('/sw.js')
def service_worker():
    sw_code = """
    const CACHE_NAME = 'rewaq-pwa-v3';
    self.addEventListener('install', (e) => self.skipWaiting());
    self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
    self.addEventListener('fetch', (e) => {});
    """
    return Response(sw_code, mimetype='application/javascript', headers={'Content-Type': 'application/javascript; charset=utf-8', 'Service-Worker-Allowed': '/'})

@app.route('/robots.txt')
def robots():
    return Response("User-agent: *\nAllow: /", mimetype='text/plain')

# --- لوحة تحكم الأدمن ---

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    # لوحة الإدارة لا تُفتح إلا بجلسة موثقة للبريد الإداري المحدد.
    if not is_admin_session():
        if request.method == 'POST':
            submitted_email = request.form.get('admin_email', '').strip().lower()
            raw_pass = request.form.get('auth_password', '')
            admin_password = os.getenv('ADMIN_PASSWORD', '')
            if submitted_email == ADMIN_EMAIL and admin_password and hmac.compare_digest(raw_pass, admin_password):
                session.clear()
                session.permanent = True
                session['logged_in'] = True
                session['admin_authenticated'] = True
                session['admin_email'] = ADMIN_EMAIL
                return redirect(url_for('admin'))
            return render_template('admin_login.html', error='بيانات دخول الإدارة غير صحيحة أو لم يتم إعداد كلمة المرور.')
        return render_template('admin_login.html')

    data = get_data(force_refresh=True)

    # 1. إرسال إشعار فوري لجميع الطلاب
    if request.method == 'POST' and 'send_broadcast' in request.form:
        b_title = request.form.get('broadcast_title', '').strip()
        b_msg = request.form.get('broadcast_msg', '').strip()
        b_url = request.form.get('broadcast_url', '/').strip() or '/'
        if b_title and b_msg:
            notify_all(b_title, b_msg, b_url)
        return redirect(url_for('admin'))

    # 2. إضافة محاضرة أو فيديو مع ريكورد صوتي اختياري
    if request.method == 'POST' and 'add_lecture' in request.form:
        sec = request.form.get('section')
        title = request.form.get('title', '').strip()
        desc = request.form.get('description', '').strip()
        link = request.form.get('link', '').strip()
        audio_link = request.form.get('audio_link', '').strip()
        video_file = request.files.get('video_file')
        
        video_url = link
        if video_file and video_file.filename and not IS_VERCEL:
            try:
                filename = f"{uuid.uuid4().hex}_{secure_filename(video_file.filename)}"
                video_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                video_url = f"/static/uploads/{filename}"
            except Exception as e:
                print("Upload save error:", e)

        if sec in ISLAMIC_SECTIONS and sec != 'catalog' and title and (video_url or audio_link):
            lec_id = str(uuid.uuid4())[:8]
            data.setdefault('islamic_sections', {}).setdefault(sec, []).append({
                'id': lec_id,
                'title': title,
                'desc': desc,
                'video_url': video_url,
                'audio_url': audio_link,
                'date': time.strftime("%Y-%m-%d")
            })
            save_data(data)
            notify_all(f"درس جديد في {ISLAMIC_SECTIONS[sec]['name']} 📖", title, f"/section/{sec}")
        return redirect(url_for('admin'))

    # 3. إضافة مذكرة / تلخيص PDF
    if request.method == 'POST' and 'add_note' in request.form:
        sec = request.form.get('section')
        title = request.form.get('note_title', '').strip()
        pdf_link = request.form.get('note_link', '').strip()
        if sec in ISLAMIC_SECTIONS and title and pdf_link:
            note_id = str(uuid.uuid4())[:8]
            data.setdefault('section_notes', {}).setdefault(sec, []).append({
                'id': note_id,
                'title': title,
                'link': pdf_link,
                'date': time.strftime("%Y-%m-%d")
            })
            save_data(data)
            notify_all(f"مذكرة جديدة في {ISLAMIC_SECTIONS[sec]['name']} 📚", title, f"/section/{sec}")
        return redirect(url_for('admin'))

    # 4. إضافة امتحان (أسئلة أو رابط خارجي)
    if request.method == 'POST' and 'add_exam' in request.form:
        sec = request.form.get('section')
        exam_title = request.form.get('exam_title', '').strip()
        exam_type = request.form.get('exam_type', 'internal')
        external_link = request.form.get('external_link', '').strip()
        questions_raw = request.form.get('questions_json', '[]')
        
        if sec in ISLAMIC_SECTIONS and sec != 'catalog' and exam_title:
            exam_id = str(uuid.uuid4())[:8]
            exam_item = {
                'id': exam_id,
                'title': exam_title,
                'type': exam_type,
                'external_link': external_link if exam_type == 'external' else '',
                'questions': []
            }
            if exam_type != 'external':
                try:
                    exam_item['questions'] = json.loads(questions_raw)
                except Exception:
                    exam_item['questions'] = []
            
            data.setdefault('exams', {}).setdefault(sec, []).append(exam_item)
            save_data(data)
            notify_all(f"اختبار جديد في {ISLAMIC_SECTIONS[sec]['name']} ✍️", exam_title, f"/section/{sec}")
        return redirect(url_for('admin'))

    users = data.get('users', [])
    watch_records = data.get('watch_records', {})
    exam_results = data.get('exam_results', {})
    banned_users = [str(b).strip() for b in data.get('banned_users', [])]
    moderators = [str(m).strip() for m in data.get('chat_moderators', [])]
    
    admin_sections = {k: v for k, v in ISLAMIC_SECTIONS.items() if k != 'catalog'}

    return render_template('admin.html', 
                           sections=admin_sections, 
                           data=data, 
                           users=users, 
                           total_users=len(users),
                           banned_users=banned_users,
                           moderators=moderators,
                           watch_records=watch_records,
                           exam_results=exam_results,
                           ad3yah=data.get('ad3yah', []),
                           azkar=data.get('azkar', {}))

# مسار تصفير وحذف حسابات الطلاب والمشاهدات
@app.route('/admin/reset_students', methods=['POST'])
def reset_students():
    if not is_admin_session():
        return redirect(url_for('admin'))
    data = get_data(force_refresh=True)
    data['users'] = []
    data['watch_records'] = {}
    data['exam_results'] = {}
    save_data(data)
    return redirect(url_for('admin'))

# --- عمليات إدارة الطلاب (حظر - فك حظر - حذف نهائي - تعيين مشرف) ---

@app.route('/admin/ban_user/<user_identifier>', methods=['POST'])
def admin_ban_user(user_identifier):
    if not is_admin_session():
        return redirect(url_for('admin'))
    data = get_data(force_refresh=True)
    banned = data.setdefault('banned_users', [])
    if str(user_identifier).strip() not in banned:
        banned.append(str(user_identifier).strip())
        save_data(data)
    return redirect(url_for('admin'))

@app.route('/admin/unban_user/<user_identifier>', methods=['POST'])
def admin_unban_user(user_identifier):
    if not is_admin_session():
        return redirect(url_for('admin'))
    data = get_data(force_refresh=True)
    banned = data.setdefault('banned_users', [])
    if str(user_identifier).strip() in banned:
        banned.remove(str(user_identifier).strip())
        save_data(data)
    return redirect(url_for('admin'))

@app.route('/admin/delete_user/<user_identifier>', methods=['POST'])
def admin_delete_user(user_identifier):
    if not is_admin_session():
        return redirect(url_for('admin'))
    data = get_data(force_refresh=True)
    data['users'] = [u for u in data.get('users', []) if str(u.get('identifier')).strip() != str(user_identifier).strip()]
    save_data(data)
    return redirect(url_for('admin'))

@app.route('/admin/toggle_moderator/<user_identifier>', methods=['POST'])
def admin_toggle_moderator(user_identifier):
    if not is_admin_session():
        return redirect(url_for('admin'))
    data = get_data(force_refresh=True)
    mods = data.setdefault('chat_moderators', [])
    u_id = str(user_identifier).strip()
    if u_id in mods:
        mods.remove(u_id)
    else:
        mods.append(u_id)
    save_data(data)
    return redirect(url_for('admin'))

@app.route('/admin/delete_lecture/<sec_key>/<lec_id>', methods=['POST'])
def delete_lecture(sec_key, lec_id):
    if not is_admin_session():
        return redirect(url_for('admin'))
    data = get_data(force_refresh=True)
    lectures = data.get('islamic_sections', {}).get(sec_key, [])
    data['islamic_sections'][sec_key] = [l for l in lectures if l.get('id') != lec_id]
    save_data(data)
    return redirect(url_for('admin'))

@app.route('/admin/delete_note/<sec_key>/<note_id>', methods=['POST'])
def delete_section_note(sec_key, note_id):
    if not is_admin_session():
        return redirect(url_for('admin'))
    data = get_data(force_refresh=True)
    notes = data.get('section_notes', {}).get(sec_key, [])
    data['section_notes'][sec_key] = [n for n in notes if n.get('id') != note_id]
    save_data(data)
    return redirect(url_for('admin'))

@app.route('/admin/delete_exam/<sec_key>/<exam_id>', methods=['POST'])
def delete_exam(sec_key, exam_id):
    if not is_admin_session():
        return redirect(url_for('admin'))
    data = get_data(force_refresh=True)
    exams = data.get('exams', {}).get(sec_key, [])
    data['exams'][sec_key] = [e for e in exams if e.get('id') != exam_id]
    save_data(data)
    return redirect(url_for('admin'))

@app.route('/admin/add_dua', methods=['POST'])
def add_dua():
    if not is_admin_session():
        return redirect(url_for('admin'))
    title = request.form.get('dua_title', '').strip()
    content = request.form.get('dua_content', '').strip()
    if title and content:
        data = get_data(force_refresh=True)
        data.setdefault('ad3yah', []).append({'title': title, 'content': content})
        save_data(data)
    return redirect(url_for('admin'))

@app.route('/admin/delete_dua/<int:index>', methods=['POST'])
def delete_dua(index):
    if not is_admin_session():
        return redirect(url_for('admin'))
    data = get_data(force_refresh=True)
    ad3yah_list = data.get('ad3yah', [])
    if 0 <= index < len(ad3yah_list):
        ad3yah_list.pop(index)
        save_data(data)
    return redirect(url_for('admin'))

@app.route('/admin/add_zekr', methods=['POST'])
def add_zekr():
    if not is_admin_session():
        return redirect(url_for('admin'))
    cat = request.form.get('category', '').strip()
    content = request.form.get('zekr_content', '').strip()
    count = int(request.form.get('zekr_count', '1') or 1)
    if cat in ['sabah', 'masea', 'nom', 'hadith'] and content:
        data = get_data(force_refresh=True)
        data.setdefault('azkar', {}).setdefault(cat, []).append({'content': content, 'count': count})
        save_data(data)
    return redirect(url_for('admin'))

@app.route('/admin/delete_zekr/<cat>/<int:index>', methods=['POST'])
def delete_zekr(cat, index):
    if not is_admin_session():
        return redirect(url_for('admin'))
    data = get_data(force_refresh=True)
    azk_list = data.get('azkar', {}).get(cat, [])
    if 0 <= index < len(azk_list):
        azk_list.pop(index)
        save_data(data)
    return redirect(url_for('admin'))

@app.route('/download_backup')
def download_backup():
    if not is_admin_session():
        return redirect(url_for('admin'))
    backup_data = get_data(force_refresh=True)
    return Response(
        json.dumps(backup_data, ensure_ascii=False, indent=4),
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment;filename=rewaq_platform_backup.json'}
    )

@app.errorhandler(500)
def handle_500(e):
    # Keep tracebacks in server logs only; never expose internal paths or secrets to visitors.
    app.logger.exception("Unhandled server error")
    return "حدث خطأ غير متوقع. حاول مرة أخرى لاحقًا.", 500

if __name__ == '__main__':
    app.run(debug=os.getenv('FLASK_DEBUG', 'false').lower() == 'true')
