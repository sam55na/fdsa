import os
import json
import threading
import time
import random
import requests
import telebot
from telebot import types
from queue import Queue
from threading import Lock
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor
from urllib.parse import urlparse
import logging

# تكوين التسجيل
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ===============================================================
# إعدادات النظام من المتغيرات البيئية
# ===============================================================

# بيانات الاعتماد من البيئة
USERNAME = os.getenv('ICHANCY_USERNAME')
PASSWORD = os.getenv('ICHANCY_PASSWORD')
BASE_URL = os.getenv('ICHANCY_BASE_URL', 'https://agents.55bets.net/')

# إعدادات البوت
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')

# مجموعات الطلبات
PAYMENT_REQUESTS_CHAT_ID = os.getenv('PAYMENT_REQUESTS_CHAT_ID')
WITHDRAWAL_REQUESTS_CHAT_ID = os.getenv('WITHDRAWAL_REQUESTS_CHAT_ID')

# إعدادات القناة
CHANNEL_USERNAME = os.getenv('CHANNEL_USERNAME')
CHANNEL_ID = os.getenv('CHANNEL_ID')
CHANNEL_LINK = os.getenv('CHANNEL_LINK')

# إعدادات PostgreSQL
DATABASE_URL = os.getenv('DATABASE_URL')

# الطابور العام للمهام
account_operations_queue = Queue()

# الأقفال
user_locks = {}
system_lock = Lock()

# الوسيط
bot = telebot.TeleBot(TELEGRAM_TOKEN)
user_data = {}

# ===============================================================
# فئة الوسيط المحسنة (Agent)
# ===============================================================

class IChancyAgent:
    def __init__(self):
        self.BASE_URL = BASE_URL
        self.USERNAME = USERNAME
        self.PASSWORD = PASSWORD
        self.session = requests.Session()
        self.logged_in = False
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36'
        ]
        self.setup_headers()
        renew_thread = threading.Thread(target=self.auto_renew_session, daemon=True)
        renew_thread.start()

    def setup_headers(self):
        self.headers = {
            "Content-Type": "application/json",
            "Origin": self.BASE_URL,
            "Referer": f"{self.BASE_URL}/",
            "User-Agent": random.choice(self.user_agents),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
            "Connection": "keep-alive"
        }

    def rotate_user_agent(self):
        self.headers["User-Agent"] = random.choice(self.user_agents)

    def direct_api_login(self):
        try:
            self.rotate_user_agent()
            login_payload = {
                "username": self.USERNAME,
                "password": self.PASSWORD
            }
            
            response = self.session.post(
                f"{self.BASE_URL}/global/api/User/signIn",
                json=login_payload,
                headers=self.headers,
                timeout=30
            )
            
            if response.status_code == 200:
                response_data = response.json()
                if response_data.get("status") and response_data.get("result", {}).get("message") == "dashboard":
                    self.logged_in = True
                    logger.info("✅ تم تسجيل الدخول بنجاح باستخدام API المباشر")
                    return True
            return False
        except Exception as e:
            logger.error(f"Direct API login error: {str(e)}")
            return False

    def ensure_login(self):
        if self.logged_in:
            return True
        if self.direct_api_login():
            return True
        return False

    def auto_renew_session(self):
        while True:
            time.sleep(250)  
            try:
                if self.logged_in:
                    print("🔄 جاري تجديد الجلسة تلقائيًا...")
                    self.logged_in = False
                    if self.ensure_login():
                        print("✅ تم تجديد الجلسة بنجاح")
                    else:
                        print("❌ فشل في تجديد الجلسة")
            except Exception as e:
                print(f"خطأ في تجديد الجلسة: {str(e)}")

    def make_request(self, endpoint, payload=None, method="POST", retries=3):
        for attempt in range(retries):
            try:
                if not self.ensure_login():
                    return {"error": "Login failed", "status": "error"}
                
                url = f"{self.BASE_URL}{endpoint}"
                self.rotate_user_agent()
                
                if method == "POST":
                    response = self.session.post(url, json=payload, headers=self.headers, timeout=30)
                else:
                    response = self.session.get(url, headers=self.headers, timeout=30)
                
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 401:
                    self.logged_in = False
                    time.sleep(2)
                    continue
                    
            except Exception as e:
                logger.error(f"Request error: {str(e)}")
                time.sleep(2)
        
        return {"error": "Request failed after retries", "status": "error"}

    def get_players(self, start=0, limit=100):
        payload = {
            "start": start,
            "limit": limit,
            "filter": {},
            "isNextPage": False,
            "searchBy": {"getPlayersFromChildrenLists": ""}
        }
        return self.make_request("/global/api/Player/getPlayersForCurrentAgent", payload)

    def get_player_balance(self, player_id):
        payload = {"playerId": player_id}
        return self.make_request("/global/api/Player/getPlayerBalanceById", payload)

    def deposit_to_player(self, player_id, amount):
        payload = {
            "playerId": str(player_id),
            "amount": amount,
            "comment": None,
            "currencyCode": "NSP",
            "currency": "NSP",
            "moneyStatus": 5
        }
        result = self.make_request("/global/api/Player/depositToPlayer", payload)
        return result.get("status", False) if result and "error" not in result else False

    def withdraw_from_player(self, player_id, amount):
        payload = {
            "playerId": str(player_id),
            "amount": -amount,
            "comment": None,
            "currencyCode": "NSP",
            "currency": "NSP",
            "moneyStatus": 5
        }
        result = self.make_request("/global/api/Player/withdrawFromPlayer", payload)
        return result.get("status", False) if result and "error" not in result else False

    def register_player(self, username, password, email):
        payload = {
            "player": {
                "email": email,
                "password": password,
                "parentId": "2474527",
                "login": username
            }
        }
        return self.make_request("/global/api/Player/registerPlayer", payload)

    def get_cashier_balance(self):
        return self.make_request("/global/api/Agent/getAgentAllWallets", method="GET")

# إنشاء كائن الوسيط
agent = IChancyAgent()

# ===============================================================
# نظام إدارة البيانات مع PostgreSQL - الإصدار المحسن
# ===============================================================

class DatabaseManager:
    def __init__(self):
        self.connection = None
        self.max_retries = 3
        self.retry_delay = 5
        self.connect_with_retry()

    def connect_with_retry(self, retry_count=0):
        try:
            database_url = os.getenv('DATABASE_URL')
            
            if not database_url:
                logger.error("❌ متغير DATABASE_URL غير محدد")
                if retry_count < self.max_retries:
                    time.sleep(self.retry_delay)
                    self.connect_with_retry(retry_count + 1)
                return
            
            if database_url.startswith('postgres://'):
                database_url = database_url.replace('postgres://', 'postgresql://', 1)
            
            self.connection = psycopg2.connect(
                database_url,
                connect_timeout=30,
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=5
            )
            self.connection.autocommit = False
            logger.info("✅ تم الاتصال بقاعدة البيانات PostgreSQL بنجاح")
            
            # إعادة إنشاء جميع الجداول بهيكل صحيح
            self.recreate_all_tables()
            
        except Exception as e:
            logger.error(f"❌ خطأ في الاتصال بقاعدة البيانات (المحاولة {retry_count + 1}): {str(e)}")
            if retry_count < self.max_retries:
                logger.info(f"🔄 إعادة المحاولة بعد {self.retry_delay} ثواني...")
                time.sleep(self.retry_delay)
                self.connect_with_retry(retry_count + 1)
            else:
                logger.error("❌ فشل جميع محاولات الاتصال بقاعدة البيانات")

    def recreate_all_tables(self):
        """إعادة إنشاء جميع الجداول بهيكل صحيح"""
        try:
            with self.connection.cursor() as cursor:
                # حذف جميع الجداول القديمة بشكل منفصل لتجنب deadlock
                tables_to_drop = [
                    'referral_commissions', 'referral_earnings', 'referral_settings', 'referrals',
                    'accounts', 'wallets', 'transactions', 'payment_methods',
                    'withdraw_methods', 'banned_users', 'system_settings',
                    'pending_withdrawals', 'payment_requests', 'maintenance',
                    'loyalty_points', 'loyalty_points_history', 'loyalty_rewards',
                    'loyalty_redemptions', 'loyalty_settings',
                    'compensation_requests', 'compensation_settings', 'first_deposit_tracking','support_requests','gift_transactions','gift_codes','gift_code_usage'
            ]
                for table in tables_to_drop:
                    try:
                        cursor.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
                    except Exception as e:
                        logger.error(f"❌ خطأ في حذف الجدول {table}: {str(e)}")

                # إنشاء الجداول بهيكل موحد وصحيح
            
                # ==================== الجداول الأساسية ====================
            
                # جدول الحسابات
                cursor.execute('''
                    CREATE TABLE accounts (
                        chat_id TEXT PRIMARY KEY,
                        username TEXT NOT NULL,
                        password TEXT NOT NULL,
                        player_id TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
            
                # جدول المحافظ
                cursor.execute('''
                    CREATE TABLE wallets (
                        chat_id TEXT PRIMARY KEY,
                        balance DECIMAL(15, 2) DEFAULT 0.0,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
            
                # جدول المعاملات
                cursor.execute('''
                    CREATE TABLE transactions (
                        transaction_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        type TEXT NOT NULL,
                        amount DECIMAL(15, 2) NOT NULL,
                        description TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
            
                # جدول طرق الدفع
                cursor.execute('''
                    CREATE TABLE payment_methods (
                        method_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        address TEXT NOT NULL,
                        min_amount DECIMAL(15, 2) NOT NULL,
                        exchange_rate DECIMAL(10, 4) DEFAULT 1.0,
                        active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
            
                # جدول طرق السحب
                cursor.execute('''
                    CREATE TABLE withdraw_methods (
                        method_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        commission_rate DECIMAL(5, 4) NOT NULL,
                        active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
            
                # جدول المستخدمين المحظورين
                cursor.execute('''
                    CREATE TABLE banned_users (
                        user_id TEXT PRIMARY KEY,
                        banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        banned_by TEXT NOT NULL
                    )
                ''')
            
                # جدول إعدادات النظام
                cursor.execute('''
                    CREATE TABLE system_settings (
                        setting_key TEXT PRIMARY KEY,
                        setting_value TEXT NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
            
                # جدول طلبات السحب المعلقة
                cursor.execute('''
                    CREATE TABLE pending_withdrawals (
                        withdrawal_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        amount DECIMAL(15, 2) NOT NULL,
                        method_id TEXT NOT NULL,
                        address TEXT NOT NULL,
                        status TEXT DEFAULT 'pending',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        completed_at TIMESTAMP,
                        group_message_id TEXT,
                        group_chat_id TEXT
                    )
                ''')
            
                # جدول طلبات الدفع
                cursor.execute('''
                    CREATE TABLE payment_requests (
                        request_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        amount DECIMAL(15, 2) NOT NULL,
                        method_id TEXT NOT NULL,
                        transaction_id TEXT NOT NULL,
                        status TEXT DEFAULT 'pending',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        approved_at TIMESTAMP,
                        rejected_at TIMESTAMP,
                        group_message_id TEXT,
                        group_chat_id TEXT
                    )
                ''')
            
                # جدول الصيانة
                cursor.execute('''
                    CREATE TABLE maintenance (
                        maintenance_key TEXT PRIMARY KEY,
                        active BOOLEAN DEFAULT FALSE,
                        message TEXT,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
            
                # ==================== نظام الإحالات ====================
            
                # جدول الإحالات
                cursor.execute('''
                    CREATE TABLE referrals (
                        referrer_id TEXT NOT NULL,
                        referred_id TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (referrer_id, referred_id)
                    )
                ''')
            
                # جدول نسب الإحالات
                cursor.execute('''
                    CREATE TABLE referral_commissions (
                        referral_id SERIAL PRIMARY KEY,
                        referrer_id TEXT NOT NULL,
                        referred_id TEXT NOT NULL,
                        transaction_type TEXT NOT NULL,
                        amount DECIMAL(15, 2) NOT NULL,
                        net_loss DECIMAL(15, 2) DEFAULT 0,
                        commission_rate DECIMAL(5, 4) DEFAULT 0.1,
                        commission_amount DECIMAL(15, 2) DEFAULT 0,
                        status TEXT DEFAULT 'pending',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        processed_at TIMESTAMP
                    )
                ''')
            
                # جدول إعدادات الإحالات
                cursor.execute('''
                    CREATE TABLE referral_settings (
                        setting_key TEXT PRIMARY KEY,
                        setting_value TEXT NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
            
                # جدول مستحقات الإحالات
                cursor.execute('''
                    CREATE TABLE referral_earnings (
                        referrer_id TEXT PRIMARY KEY,
                        pending_commission DECIMAL(15, 2) DEFAULT 0,
                        total_commission DECIMAL(15, 2) DEFAULT 0,
                        last_payout TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
            
                # ==================== نظام نقاط الامتياز ====================
            
                # جدول نقاط الامتياز
                cursor.execute('''
                    CREATE TABLE loyalty_points (
                        user_id TEXT PRIMARY KEY,
                        points INTEGER DEFAULT 0,
                        last_reset TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
            
                # جدول سجل نقاط الامتياز
                cursor.execute('''
                    CREATE TABLE loyalty_points_history (
                        history_id SERIAL PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        points_change INTEGER NOT NULL,
                        reason TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
            
                # جدول الجوائز
                cursor.execute('''
                    CREATE TABLE loyalty_rewards (
                        reward_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        description TEXT,
                        points_cost INTEGER NOT NULL,
                        discount_rate DECIMAL(5,2) DEFAULT 0,
                        active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
            
                # جدول طلبات استبدال النقاط
                cursor.execute('''
                    CREATE TABLE loyalty_redemptions (
                        redemption_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        reward_id TEXT NOT NULL,
                        points_cost INTEGER NOT NULL,
                        status TEXT DEFAULT 'pending',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        processed_at TIMESTAMP,
                        admin_notes TEXT
                    )
                ''')
            
                # جدول إعدادات نظام النقاط
                cursor.execute('''
                    CREATE TABLE loyalty_settings (
                        setting_key TEXT PRIMARY KEY,
                        setting_value TEXT NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
            
                # ==================== نظام التعويض ====================
            
                # جدول طلبات التعويض
                cursor.execute('''
                    CREATE TABLE compensation_requests (
                        request_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        amount DECIMAL(15, 2) NOT NULL,
                        net_loss DECIMAL(15, 2) NOT NULL,
                        status TEXT DEFAULT 'pending',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        approved_at TIMESTAMP,
                        rejected_at TIMESTAMP,
                        group_message_id TEXT,
                        group_chat_id TEXT
                    )
                ''')
            
                # جدول إعدادات نظام التعويض
                cursor.execute('''
                    CREATE TABLE compensation_settings (
                        setting_key TEXT PRIMARY KEY,
                        setting_value TEXT NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
            
                # ==================== جداول إضافية ====================
            
                # جدول تتبع أول إيداع
                cursor.execute('''
                    CREATE TABLE first_deposit_tracking (
                        user_id TEXT PRIMARY KEY,
                        referrer_id TEXT NOT NULL,
                        bonus_awarded BOOLEAN DEFAULT FALSE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
            
                # ==================== إدخال البيانات الافتراضية ====================
            
                # إدخال إعدادات الصيانة الافتراضية
                cursor.execute('''
                    INSERT INTO maintenance (maintenance_key, active, message) 
                    VALUES ('main', FALSE, 'البوت في حالة صيانة مؤقتة، يرجى التحلي بالصبر.')
                    ON CONFLICT (maintenance_key) DO NOTHING
                ''')
            
                # إدخال إعدادات الإحالات الافتراضية
                next_payout = datetime.now() + timedelta(days=10)
                cursor.execute('''
                    INSERT INTO referral_settings (setting_key, setting_value) 
                    VALUES 
                        ('commission_rate', '0.1'),
                        ('payout_days', '10'),
                        ('last_payout_date', %s),
                        ('next_payout_date', %s)
                    ON CONFLICT (setting_key) DO NOTHING
                ''', (datetime.now().isoformat(), next_payout.isoformat()))
            
                # إدخال إعدادات نقاط الامتياز الافتراضية
                cursor.execute('''
                    INSERT INTO loyalty_settings (setting_key, setting_value) VALUES 
                        ('points_per_10000', '1'),
                        ('min_redemption_points', '100'),
                        ('reset_days', '30'),
                        ('redemption_enabled', 'false'),
                        ('referral_points', '1'),
                        ('first_deposit_bonus', '3')
                ON CONFLICT (setting_key) DO NOTHING
                ''')
            
                # إدخال إعدادات التعويض الافتراضية
                cursor.execute('''
                    INSERT INTO compensation_settings (setting_key, setting_value) VALUES 
                        ('compensation_rate', '0.1'),
                        ('min_loss_amount', '10000'),
                        ('compensation_enabled', 'true')
                    ON CONFLICT (setting_key) DO NOTHING
                ''')
            
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS compensation_tracking (
                        user_id TEXT PRIMARY KEY,
                        last_compensation_loss DECIMAL(15, 2) DEFAULT 0,
                        last_compensation_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
            
                # جدول طلبات الدعم
                cursor.execute("""
                CREATE TABLE support_requests (
                    request_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    username TEXT,
                    message_text TEXT,
                    photo_id TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    admin_chat_id TEXT,
                    admin_message_id TEXT
)
                """)
                cursor.execute("""
                CREATE TABLE gift_transactions (
                    gift_id TEXT PRIMARY KEY,
                    from_user_id TEXT NOT NULL,
                    to_user_id TEXT NOT NULL,
                    amount DECIMAL(15, 2) NOT NULL,
                    commission DECIMAL(15, 2) NOT NULL,
                    net_amount DECIMAL(15, 2) NOT NULL,
                    status TEXT DEFAULT 'completed',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
                """)
                
                # جدول أكواد الهدايا
                cursor.execute("""
                CREATE TABLE gift_codes (
                    code TEXT PRIMARY KEY,
                    amount DECIMAL(15, 2) NOT NULL,
                    max_uses INTEGER NOT NULL,
                    used_count INTEGER DEFAULT 0,
                    created_by TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP,
                    active BOOLEAN DEFAULT TRUE
)
""")

                # جدول استخدامات أكواد الهدايا
                cursor.execute("""
                CREATE TABLE gift_code_usage (
                    usage_id SERIAL PRIMARY KEY,
                    code TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    amount_received DECIMAL(15, 2) NOT NULL
)
""")
                
                
                # إدخال الجوائز الافتراضية
                default_rewards = [
                    ('reward_1', '10$', 'رصيد 10 دولار', 250, 0),
                    ('reward_2', '100$', 'رصيد 100 دولار', 2500, 7),
                    ('reward_3', 'Apple AirPods Pro 3', 'سماعات أبل برو 3', 4500, 0),
                    ('reward_4', 'XBOX Series X', 'جهاز إكس بوكس سيريس X', 10000, 0),
                    ('reward_5', 'PlayStation 5', 'جهاز بلايستيشن 5', 10500, 0),
                    ('reward_6', '500$', 'رصيد 500 دولار', 12500, 10),
                    ('reward_7', 'GOLD Coin', 'عملة ذهبية', 16000, 0),
                    ('reward_8', 'Samsung Galaxy S25 Ultra', 'سامسونج جلاكسي S25 الترا', 22000, 0),
                    ('reward_9', 'iPhone 16 Pro Max', 'آيفون 16 برو ماكس', 28000, 0)
                ]
            
                for reward in default_rewards:
                    cursor.execute('''
                        INSERT INTO loyalty_rewards (reward_id, name, description, points_cost, discount_rate)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (reward_id) DO NOTHING
                    ''', reward)

                self.connection.commit()
                logger.info("✅ تم إنشاء جميع الجداول بنجاح بهيكل موحد ومصحح")
            
        except Exception as e:
            logger.error(f"❌ خطأ في إنشاء الجداول: {str(e)}")
            if self.connection:
                self.connection.rollback()

    def execute_query(self, query, params=None):
        try:
            if not self.connection or self.connection.closed:
                self.reconnect()
                if not self.connection:
                    return False
                    
            with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(query, params or ())
                if query.strip().upper().startswith('SELECT'):
                    result = cursor.fetchall()
                    return result
                self.connection.commit()
                return True
        except psycopg2.InterfaceError:
            logger.warning("🔄 إعادة الاتصال بقاعدة البيانات...")
            self.reconnect()
            return False
        except Exception as e:
            logger.error(f"❌ خطأ في تنفيذ الاستعلام: {str(e)}")
            if self.connection:
                self.connection.rollback()
            return False

    def reconnect(self):
        try:
            if self.connection:
                self.connection.close()
            self.connect_with_retry()
        except Exception as e:
            logger.error(f"❌ خطأ في إعادة الاتصال: {str(e)}")

# إنشاء مدير قاعدة البيانات
db_manager = DatabaseManager()

# ===============================================================
# دوال المساعدة المحسنة مع الهيكل الجديد
# ===============================================================

def get_wallet_balance(chat_id):
    """جلب رصيد محفظة المستخدم"""
    result = db_manager.execute_query(
        'SELECT balance FROM wallets WHERE chat_id = %s',
        (str(chat_id),)
    )
    if result and len(result) > 0:
        balance = result[0]['balance']
        # تحويل decimal إلى float
        return float(balance) if balance is not None else 0.0
    
    # إذا لم يكن للمستخدم محفظة، إنشاء واحدة برصيد 0
    db_manager.execute_query(
        'INSERT INTO wallets (chat_id, balance) VALUES (%s, 0) ON CONFLICT (chat_id) DO NOTHING',
        (str(chat_id),)
    )
    return 0.0

def update_wallet_balance(chat_id, amount):
    """تحديث رصيد محفظة المستخدم"""
    try:
        current_balance = get_wallet_balance(chat_id)
        
        # تحويل جميع القيم إلى float لتجنب مشكلة decimal
        current_balance_float = float(current_balance)
        amount_float = float(amount)
        new_balance = current_balance_float + amount_float
        
        success = db_manager.execute_query(
            """INSERT INTO wallets (chat_id, balance) 
               VALUES (%s, %s) 
               ON CONFLICT (chat_id) 
               DO UPDATE SET balance = EXCLUDED.balance, updated_at = CURRENT_TIMESTAMP""",
            (str(chat_id), new_balance)
        )
        
        if success:
            logger.info(f"تم تحديث رصيد المحفظة {chat_id}: {current_balance} -> {new_balance} ✔")
            return new_balance
        else:
            logger.error(f"فشل في تحديث رصيد المحفظة {chat_id}: {current_balance} ✘")
            return current_balance
            
    except Exception as e:
        logger.error(f"خطأ في تحديث رصيد المحفظة: {str(e)} ✘")
        return get_wallet_balance(chat_id)

def load_accounts():
    """تحميل جميع الحسابات"""
    result = db_manager.execute_query('SELECT * FROM accounts')
    accounts = {}
    if result:
        for row in result:
            accounts[row['chat_id']] = {
                'username': row['username'],
                'password': row['password'],
                'playerId': row['player_id'],
                'created_at': row['created_at'].timestamp() if row['created_at'] else time.time()
            }
    return accounts

def save_accounts(accounts):
    """حفظ جميع الحسابات"""
    for chat_id, account_data in accounts.items():
        success = db_manager.execute_query('''
            INSERT INTO accounts (chat_id, username, password, player_id) 
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (chat_id) 
            DO UPDATE SET 
                username = EXCLUDED.username,
                password = EXCLUDED.password,
                player_id = EXCLUDED.player_id
        ''', (
            str(chat_id),
            account_data.get('username'),
            account_data.get('password'),
            account_data.get('playerId')
        ))
        if not success:
            return False
    return True

def load_payment_methods():
    """تحميل طرق الدفع"""
    result = db_manager.execute_query('SELECT * FROM payment_methods')
    methods = {}
    if result:
        for row in result:
            methods[row['method_id']] = {
                'name': row['name'],
                'address': row['address'],
                'min_amount': float(row['min_amount']),
                'exchange_rate': float(row['exchange_rate']),
                'active': row['active']
            }
    return methods

def save_payment_methods(methods):
    """حفظ طرق الدفع"""
    for method_id, method_data in methods.items():
        success = db_manager.execute_query('''
            INSERT INTO payment_methods (method_id, name, address, min_amount, exchange_rate, active) 
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (method_id) 
            DO UPDATE SET 
                name = EXCLUDED.name,
                address = EXCLUDED.address,
                min_amount = EXCLUDED.min_amount,
                exchange_rate = EXCLUDED.exchange_rate,
                active = EXCLUDED.active
        ''', (
            method_id,
            method_data.get('name'),
            method_data.get('address'),
            method_data.get('min_amount'),
            method_data.get('exchange_rate', 1.0),
            method_data.get('active', True)
        ))
        if not success:
            return False
    return True

def load_withdraw_methods():
    """تحميل طرق السحب"""
    result = db_manager.execute_query('SELECT * FROM withdraw_methods')
    methods = {}
    if result:
        for row in result:
            methods[row['method_id']] = {
                'name': row['name'],
                'commission_rate': float(row['commission_rate']),
                'active': row['active']
            }
    return methods

def save_withdraw_methods(methods):
    """حفظ طرق السحب"""
    for method_id, method_data in methods.items():
        success = db_manager.execute_query('''
            INSERT INTO withdraw_methods (method_id, name, commission_rate, active) 
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (method_id) 
            DO UPDATE SET 
                name = EXCLUDED.name,
                commission_rate = EXCLUDED.commission_rate,
                active = EXCLUDED.active
        ''', (
            method_id,
            method_data.get('name'),
            method_data.get('commission_rate'),
            method_data.get('active', True)
        ))
        if not success:
            return False
    return True

def load_transactions():
    """تحميل المعاملات"""
    result = db_manager.execute_query('SELECT * FROM transactions')
    transactions = {}
    if result:
        for row in result:
            transactions[row['transaction_id']] = {
                'user_id': row['user_id'],
                'type': row['type'],
                'amount': float(row['amount']),
                'description': row['description'],
                'created_at': row['created_at'].timestamp() if row['created_at'] else time.time()
            }
    return transactions

def save_transactions(transactions):
    """حفظ المعاملات"""
    for transaction_id, transaction_data in transactions.items():
        success = db_manager.execute_query('''
            INSERT INTO transactions (transaction_id, user_id, type, amount, description) 
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (transaction_id) 
            DO UPDATE SET 
                user_id = EXCLUDED.user_id,
                type = EXCLUDED.type,
                amount = EXCLUDED.amount,
                description = EXCLUDED.description
        ''', (
            transaction_id,
            transaction_data.get('user_id'),
            transaction_data.get('type'),
            transaction_data.get('amount'),
            transaction_data.get('description')
        ))
        if not success:
            return False
    return True

def load_maintenance():
    """تحميل إعدادات الصيانة"""
    result = db_manager.execute_query('SELECT * FROM maintenance WHERE maintenance_key = %s', ('main',))
    if result and len(result) > 0:
        return {
            'active': result[0]['active'],
            'message': result[0]['message']
        }
    return {'active': False, 'message': 'البوت في حالة صيانة مؤقتة، يرجى التحلي بالصبر.'}

def save_maintenance(maintenance):
    """حفظ إعدادات الصيانة"""
    return db_manager.execute_query('''
        INSERT INTO maintenance (maintenance_key, active, message) 
        VALUES ('main', %s, %s)
        ON CONFLICT (maintenance_key) 
        DO UPDATE SET 
            active = EXCLUDED.active,
            message = EXCLUDED.message,
            updated_at = CURRENT_TIMESTAMP
    ''', (maintenance.get('active', False), maintenance.get('message', '')))

def load_pending_withdrawals():
    """تحميل طلبات السحب المعلقة"""
    result = db_manager.execute_query('SELECT * FROM pending_withdrawals WHERE status = %s', ('pending',))
    withdrawals = {}
    if result:
        for row in result:
            withdrawals[row['withdrawal_id']] = {
                'user_id': row['user_id'],
                'amount': float(row['amount']),
                'method_id': row['method_id'],
                'address': row['address'],
                'timestamp': row['created_at'].timestamp() if row['created_at'] else time.time(),
                'status': row['status'],
                'group_message_id': row['group_message_id'],
                'group_chat_id': row['group_chat_id']
            }
    return withdrawals

def save_pending_withdrawals(withdrawals):
    """حفظ طلبات السحب المعلقة"""
    for withdrawal_id, withdrawal_data in withdrawals.items():
        success = db_manager.execute_query('''
            INSERT INTO pending_withdrawals (withdrawal_id, user_id, amount, method_id, address, status, group_message_id, group_chat_id) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (withdrawal_id) 
            DO UPDATE SET 
                user_id = EXCLUDED.user_id,
                amount = EXCLUDED.amount,
                method_id = EXCLUDED.method_id,
                address = EXCLUDED.address,
                status = EXCLUDED.status,
                group_message_id = EXCLUDED.group_message_id,
                group_chat_id = EXCLUDED.group_chat_id
        ''', (
            withdrawal_id,
            withdrawal_data.get('user_id'),
            withdrawal_data.get('amount'),
            withdrawal_data.get('method_id'),
            withdrawal_data.get('address'),
            withdrawal_data.get('status', 'pending'),
            withdrawal_data.get('group_message_id'),
            withdrawal_data.get('group_chat_id')
        ))
        if not success:
            return False
    return True

def load_payment_requests():
    """تحميل طلبات الدفع"""
    result = db_manager.execute_query('SELECT * FROM payment_requests WHERE status = %s', ('pending',))
    requests = {}
    if result:
        for row in result:
            requests[row['request_id']] = {
                'user_id': row['user_id'],
                'amount': float(row['amount']),
                'method_id': row['method_id'],
                'transaction_id': row['transaction_id'],
                'timestamp': row['created_at'].timestamp() if row['created_at'] else time.time(),
                'status': row['status'],
                'group_message_id': row['group_message_id'],
                'group_chat_id': row['group_chat_id']
            }
    return requests

def save_payment_requests(requests):
    """حفظ طلبات الدفع"""
    for request_id, request_data in requests.items():
        success = db_manager.execute_query('''
            INSERT INTO payment_requests (request_id, user_id, amount, method_id, transaction_id, status, group_message_id, group_chat_id) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (request_id) 
            DO UPDATE SET 
                user_id = EXCLUDED.user_id,
                amount = EXCLUDED.amount,
                method_id = EXCLUDED.method_id,
                transaction_id = EXCLUDED.transaction_id,
                status = EXCLUDED.status,
                group_message_id = EXCLUDED.group_message_id,
                group_chat_id = EXCLUDED.group_chat_id
        ''', (
            request_id,
            request_data.get('user_id'),
            request_data.get('amount'),
            request_data.get('method_id'),
            request_data.get('transaction_id'),
            request_data.get('status', 'pending'),
            request_data.get('group_message_id'),
            request_data.get('group_chat_id')
        ))
        if not success:
            return False
    return True

def add_pending_withdrawal(user_id, amount, method_id, address, message_id=None, group_chat_id=None):
    """إضافة طلب سحب معلق"""
    withdrawal_id = str(int(time.time() * 1000))
    
    success = db_manager.execute_query('''
        INSERT INTO pending_withdrawals (withdrawal_id, user_id, amount, method_id, address, group_message_id, group_chat_id) 
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    ''', (withdrawal_id, str(user_id), amount, method_id, address, message_id, group_chat_id))
    
    return withdrawal_id if success else None

def remove_pending_withdrawal(withdrawal_id):
    """حذف طلب سحب معلق"""
    return db_manager.execute_query(
        'DELETE FROM pending_withdrawals WHERE withdrawal_id = %s', 
        (withdrawal_id,)
    )

def get_user_pending_withdrawal(user_id):
    """جلب طلب السحب المعلق للمستخدم"""
    result = db_manager.execute_query(
        'SELECT * FROM pending_withdrawals WHERE user_id = %s AND status = %s', 
        (str(user_id), 'pending')
    )
    if result and len(result) > 0:
        row = result[0]
        return row['withdrawal_id'], {
            'user_id': row['user_id'],
            'amount': float(row['amount']),
            'method_id': row['method_id'],
            'address': row['address'],
            'timestamp': row['created_at'].timestamp() if row['created_at'] else time.time(),
            'status': row['status'],
            'group_message_id': row['group_message_id'],
            'group_chat_id': row['group_chat_id']
        }
    return None, None

def add_payment_request(user_id, amount, method_id, transaction_id, message_id=None, group_chat_id=None):
    """إضافة طلب دفع"""
    request_id = str(int(time.time() * 1000))
    
    success = db_manager.execute_query('''
        INSERT INTO payment_requests (request_id, user_id, amount, method_id, transaction_id, group_message_id, group_chat_id) 
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    ''', (request_id, str(user_id), amount, method_id, transaction_id, message_id, group_chat_id))
    
    return request_id if success else None

def remove_payment_request(request_id):
    """حذف طلب دفع"""
    return db_manager.execute_query(
        'DELETE FROM payment_requests WHERE request_id = %s', 
        (request_id,)
    )

def get_payment_request_by_message(group_chat_id, message_id):
    """جلب طلب الدفع باستخدام معرف الرسالة"""
    result = db_manager.execute_query(
        'SELECT * FROM payment_requests WHERE group_chat_id = %s AND group_message_id = %s AND status = %s', 
        (str(group_chat_id), message_id, 'pending')
    )
    if result and len(result) > 0:
        row = result[0]
        return row['request_id'], {
            'user_id': row['user_id'],
            'amount': float(row['amount']),
            'method_id': row['method_id'],
            'transaction_id': row['transaction_id'],
            'timestamp': row['created_at'].timestamp() if row['created_at'] else time.time(),
            'status': row['status'],
            'group_message_id': row['group_message_id'],
            'group_chat_id': row['group_chat_id']
        }
    return None, None

def get_withdrawal_by_message(group_chat_id, message_id):
    """جلب طلب السحب باستخدام معرف الرسالة"""
    result = db_manager.execute_query(
        'SELECT * FROM pending_withdrawals WHERE group_chat_id = %s AND group_message_id = %s AND status = %s', 
        (str(group_chat_id), message_id, 'pending')
    )
    if result and len(result) > 0:
        row = result[0]
        return row['withdrawal_id'], {
            'user_id': row['user_id'],
            'amount': float(row['amount']),
            'method_id': row['method_id'],
            'address': row['address'],
            'timestamp': row['created_at'].timestamp() if row['created_at'] else time.time(),
            'status': row['status'],
            'group_message_id': row['group_message_id'],
            'group_chat_id': row['group_chat_id']
        }
    return None, None


def get_user_pending_withdrawal_from_group(user_id):
    """جلب طلب السحب المعلق للمستخدم من مجموعة الطلبات"""
    result = db_manager.execute_query(
        'SELECT * FROM pending_withdrawals WHERE user_id = %s AND status = %s',
        (str(user_id), 'pending')
    )
    
    if result and len(result) > 0:
        row = result[0]
        # التحقق من أن الطلب لم يتم استرداده
        if row['status'] != 'refunded':
            return {
                'withdrawal_id': row['withdrawal_id'],
                'user_id': row['user_id'],
                'amount': float(row['amount']),
                'method_id': row['method_id'],
                'address': row['address'],
                'timestamp': row['created_at'].timestamp() if row['created_at'] else time.time(),
                'status': row['status'],
                'group_message_id': row['group_message_id'],
                'group_chat_id': row['group_chat_id']
            }
    return None

def is_withdrawal_refunded(withdrawal_id):
    """التحقق إذا كان طلب السحب تم استرداده مسبقاً"""
    result = db_manager.execute_query(
        'SELECT status FROM pending_withdrawals WHERE withdrawal_id = %s',
        (withdrawal_id,)
    )
    
    if result and len(result) > 0:
        status = result[0]['status']
        return status == 'refunded'
    return False



def is_admin(chat_id):
    """التحقق إذا كان المستخدم مشرف"""
    return str(chat_id) == ADMIN_CHAT_ID

def is_user_banned(user_id):
    """التحقق إذا كان المستخدم محظور"""
    result = db_manager.execute_query(
        'SELECT 1 FROM banned_users WHERE user_id = %s', 
        (str(user_id),)
    )
    return bool(result and len(result) > 0)

def ban_user(user_id):
    """حظر مستخدم"""
    return db_manager.execute_query(
        'INSERT INTO banned_users (user_id, banned_by) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING',
        (str(user_id), ADMIN_CHAT_ID)
    )

def unban_user(user_id):
    """فك حظر مستخدم"""
    return db_manager.execute_query(
        'DELETE FROM banned_users WHERE user_id = %s',
        (str(user_id),)
    )

def is_maintenance_mode():
    """التحقق من وضع الصيانة"""
    maintenance = load_maintenance()
    return maintenance.get('active', False)

def is_user_subscribed(user_id):
    """التحقق من اشتراك المستخدم في القناة"""
    try:
        chat_member = bot.get_chat_member(CHANNEL_ID, user_id)
        return chat_member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"❌ خطأ في التحقق من الاشتراك: {str(e)}")
        return False

def generate_suffix():
    """إنشاء لاحقة عشوائية"""
    return ''.join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=4))

def add_transaction(transaction_data):
    """إضافة معاملة جديدة"""
    transaction_id = str(int(time.time() * 1000))
    
    success = db_manager.execute_query(
        "INSERT INTO transactions (transaction_id, user_id, type, amount, description) "
        "VALUES (%s, %s, %s, %s, %s)",
        (transaction_id, transaction_data.get('user_id'), transaction_data.get('type'),
         transaction_data.get('amount'), transaction_data.get('description'))
    )
    
    return transaction_id if success else None  # ✅ إرجاع transaction_id

def get_cashier_balance_via_agent():
    result = agent.get_cashier_balance()
    if result and "error" not in result:
        wallets = result.get("wallets", []) or result.get("result", [])
        if isinstance(wallets, list) and len(wallets) > 0:
            return float(wallets[0].get("balance", 0))
    return 0.0

def check_cashier_balance_sufficient(amount):
    """التحقق من كفاية رصيد الكاشير"""
    cashier_balance = get_cashier_balance_via_agent()
    logger.info(f"رصيد الكاشير الحالي: {cashier_balance}, المبلغ المطلوب: {amount}")
    return cashier_balance >= amount

def send_to_payment_group(message_text, reply_markup=None):
    """إرسال رسالة لمجموعة الدفع"""
    try:
        if PAYMENT_REQUESTS_CHAT_ID:
            result = bot.send_message(
                PAYMENT_REQUESTS_CHAT_ID, 
                message_text,
                parse_mode="HTML", 
                reply_markup=reply_markup
            )
            return result.message_id
    except Exception as e:
        logger.error(f"❌ خطأ في إرسال رسالة لمجموعة الدفع: {e}")
        try:
            result = bot.send_message(
                ADMIN_CHAT_ID, 
                message_text, 
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            return result.message_id
        except:
            return None
    return None

def send_to_withdraw_group(message_text, reply_markup=None):
    """إرسال رسالة لمجموعة السحب"""
    try:
        if WITHDRAWAL_REQUESTS_CHAT_ID:
            result = bot.send_message(
                WITHDRAWAL_REQUESTS_CHAT_ID, 
                message_text,
                parse_mode="HTML", 
                reply_markup=reply_markup
            )
            return result.message_id
    except Exception as e:
        logger.error(f"❌ خطأ في إرسال رسالة لمجموعة السحب: {e}")
        try:
            result = bot.send_message(
                ADMIN_CHAT_ID, 
                message_text, 
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            return result.message_id
        except:
            return None
    return None

def edit_group_message(chat_id, message_id, new_text, reply_markup=None):
    """تعديل رسالة في المجموعة"""
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=new_text,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        return True
    except Exception as e:
        logger.error(f"❌ خطأ في تعديل الرسالة: {e}")
        return False

def delete_group_message(chat_id, message_id):
    """حذف رسالة من المجموعة"""
    try:
        bot.delete_message(chat_id, message_id)
        return True
    except Exception as e:
        logger.error(f"❌ خطأ في حذف الرسالة: {e}")
        return False


def get_loyalty_points(user_id):
    """جلب نقاط امتياز المستخدم"""
    result = db_manager.execute_query(
        'SELECT points FROM loyalty_points WHERE user_id = %s',
        (str(user_id),)
    )
    if result and len(result) > 0:
        return result[0]['points']
    
    # إنشاء سجل جديد إذا لم يكن موجوداً
    db_manager.execute_query(
        'INSERT INTO loyalty_points (user_id, points) VALUES (%s, 0) ON CONFLICT (user_id) DO NOTHING',
        (str(user_id),)
    )
    return 0

def add_loyalty_points(user_id, points, reason):
    """إضافة نقاط امتياز للمستخدم"""
    try:
        # التحقق من انتهاء فترة التصفير
        result = db_manager.execute_query(
            'SELECT last_reset FROM loyalty_points WHERE user_id = %s',
            (str(user_id),)
        )
        
        if result and len(result) > 0:
            last_reset = result[0]['last_reset']
            reset_days = int(load_loyalty_settings().get('reset_days', 30))
            if (datetime.now() - last_reset).days >= reset_days:
                # تصفير النقاط
                db_manager.execute_query(
                    'UPDATE loyalty_points SET points = 0, last_reset = CURRENT_TIMESTAMP WHERE user_id = %s',
                    (str(user_id),)
                )
        
        # إضافة النقاط
        success = db_manager.execute_query("""
            INSERT INTO loyalty_points (user_id, points) 
            VALUES (%s, %s)
            ON CONFLICT (user_id) 
            DO UPDATE SET points = loyalty_points.points + EXCLUDED.points,
                         updated_at = CURRENT_TIMESTAMP
        """, (str(user_id), points))
        
        if success:
            # تسجيل في السجل
            db_manager.execute_query("""
                INSERT INTO loyalty_points_history (user_id, points_change, reason)
                VALUES (%s, %s, %s)
            """, (str(user_id), points, reason))
        
        return success
    except Exception as e:
        logger.error(f"خطأ في إضافة نقاط الامتياز: {str(e)}")
        return False

def load_loyalty_settings():
    """تحميل إعدادات نظام النقاط"""
    result = db_manager.execute_query('SELECT * FROM loyalty_settings')
    settings = {}
    if result:
        for row in result:
            settings[row['setting_key']] = row['setting_value']
    
    # القيم الافتراضية
    defaults = {
        'points_per_10000': '1',
        'min_redemption_points': '100',
        'reset_days': '30',
        'redemption_enabled': 'false',
        'referral_points': '1',
        'first_deposit_bonus': '3'
    }
    
    for key, value in defaults.items():
        if key not in settings:
            settings[key] = value
    
    return settings

def save_loyalty_settings(settings):
    """حفظ إعدادات نظام النقاط"""
    for key, value in settings.items():
        success = db_manager.execute_query("""
            INSERT INTO loyalty_settings (setting_key, setting_value)
            VALUES (%s, %s)
            ON CONFLICT (setting_key)
            DO UPDATE SET setting_value = EXCLUDED.setting_value,
                         updated_at = CURRENT_TIMESTAMP
        """, (key, str(value)))
        if not success:
            return False
    return True

def get_top_users_by_points(limit=10):
    """جلب أفضل المستخدمين حسب النقاط"""
    result = db_manager.execute_query("""
        SELECT user_id, points 
        FROM loyalty_points 
        WHERE points > 0 
        ORDER BY points DESC 
        LIMIT %s
    """, (limit,))
    return result if result else []

def get_loyalty_rewards():
    """جلب الجوائز المتاحة"""
    result = db_manager.execute_query("""
        SELECT * FROM loyalty_rewards 
        WHERE active = TRUE 
        ORDER BY points_cost
    """)
    rewards = {}
    if result:
        for row in result:
            rewards[row['reward_id']] = {
                'name': row['name'],
                'description': row['description'],
                'points_cost': row['points_cost'],
                'discount_rate': float(row['discount_rate']),
                'active': row['active']
            }
    return rewards

def create_redemption_request(user_id, reward_id):
    """إنشاء طلب استبدال نقاط"""
    try:
        # التحقق من الجائزة
        reward_result = db_manager.execute_query(
            'SELECT * FROM loyalty_rewards WHERE reward_id = %s AND active = TRUE',
            (reward_id,)
        )
        if not reward_result:
            return None, "الجائزة غير متاحة"
        
        reward = reward_result[0]
        points_cost = reward['points_cost']
        
        # التحقق من رصيد النقاط
        user_points = get_loyalty_points(user_id)
        if user_points < points_cost:
            return None, "نقاطك غير كافية"
        
        # التحقق من الحد الأدنى للاستبدال
        settings = load_loyalty_settings()
        min_points = int(settings.get('min_redemption_points', 100))
        if user_points < min_points:
            return None, f"الحد الأدنى للاستبدال هو {min_points} نقطة"
        
        # التحقق من تفعيل النظام
        if settings.get('redemption_enabled', 'false') != 'true':
            return None, "نظام الاستبدال غير مفعل حالياً"
        
        # خصم النقاط
        success = db_manager.execute_query("""
            UPDATE loyalty_points 
            SET points = points - %s 
            WHERE user_id = %s AND points >= %s
        """, (points_cost, str(user_id), points_cost))
        
        if not success:
            return None, "فشل في خصم النقاط"
        
        # إنشاء طلب الاستبدال
        redemption_id = f"redemption_{int(time.time() * 1000)}"
        success = db_manager.execute_query("""
            INSERT INTO loyalty_redemptions 
            (redemption_id, user_id, reward_id, points_cost)
            VALUES (%s, %s, %s, %s)
        """, (redemption_id, str(user_id), reward_id, points_cost))
        
        if success:
            # تسجيل في السجل
            db_manager.execute_query("""
                INSERT INTO loyalty_points_history 
                (user_id, points_change, reason)
                VALUES (%s, %s, %s)
            """, (str(user_id), -points_cost, f"استبدال لنقاط - {reward['name']}"))
            
            return redemption_id, "تم إنشاء طلب الاستبدال بنجاح"
        else:
            # استرجاع النقاط إذا فشل إنشاء الطلب
            db_manager.execute_query("""
                UPDATE loyalty_points 
                SET points = points + %s 
                WHERE user_id = %s
            """, (points_cost, str(user_id)))
            return None, "فشل في إنشاء طلب الاستبدال"
    
    except Exception as e:
        logger.error(f"خطأ في إنشاء طلب الاستبدال: {str(e)}")
        return None, "حدث خطأ أثناء إنشاء الطلب"

def get_user_redemption_history(user_id):
    """جلب سجل استبدال النقاط للمستخدم"""
    result = db_manager.execute_query("""
        SELECT lr.*, lrw.name as reward_name
        FROM loyalty_redemptions lr
        JOIN loyalty_rewards lrw ON lr.reward_id = lrw.reward_id
        WHERE lr.user_id = %s
        ORDER BY lr.created_at DESC
        LIMIT 20
    """, (str(user_id),))
    return result if result else []

def get_pending_redemptions():
    """جلب طلبات الاستبدال المعلقة"""
    result = db_manager.execute_query("""
        SELECT lr.*, lrw.name as reward_name, lp.user_id
        FROM loyalty_redemptions lr
        JOIN loyalty_rewards lrw ON lr.reward_id = lrw.reward_id
        JOIN loyalty_points lp ON lr.user_id = lp.user_id
        WHERE lr.status = 'pending'
        ORDER BY lr.created_at DESC
    """)
    return result if result else []

def handle_refund_last_withdrawal(call, chat_id, message_id):
    """معالجة طلب استرداد آخر طلب سحب"""
    try:
        # جلب طلب السحب المعلق للمستخدم
        withdrawal_data = get_user_pending_withdrawal_from_group(chat_id)
        
        if not withdrawal_data:
            bot.answer_callback_query(call.id, 
                                    "❌ لا توجد لديك طلبات سحب معلقة للاسترداد", 
                                    show_alert=True)
            return
        
        withdrawal_id = withdrawal_data['withdrawal_id']
        amount = withdrawal_data['amount']
        
        # التحقق إذا كان الطلب تم استرداده مسبقاً
        if is_withdrawal_refunded(withdrawal_id):
            bot.answer_callback_query(call.id, 
                                    "❌ تم استرداد هذا الطلب مسبقاً", 
                                    show_alert=True)
            return
        
        # عرض تفاصيل الطلب مع زر الاسترداد
        withdrawal_method = withdraw_system.methods.get(withdrawal_data['method_id'], {})
        method_name = withdrawal_method.get('name', 'غير معروف')
        
        text = f"""
<b>🔄 استرداد طلب السحب</b>

<b>تفاصيل الطلب:</b>
• المبلغ: <b>{amount:.2f}</b>
• الطريقة: <b>{method_name}</b>
• العنوان: <code>{withdrawal_data['address']}</code>
• الوقت: <b>{datetime.fromtimestamp(withdrawal_data['timestamp']).strftime('%Y-%m-%d %H:%M')}</b>

<b>⚠️ تأكيد الاسترداد:</b>
سيتم إعادة المبلغ إلى محفظتك وحذف الطلب من قائمة الانتظار.
        """
        
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton("✅ تأكيد الاسترداد", 
                                     callback_data=f"confirm_refund_{withdrawal_id}"),
            types.InlineKeyboardButton("❌ إلغاء", 
                                     callback_data="main_menu")
        )
        
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )
        
    except Exception as e:
        logger.error(f"خطأ في معالجة استرداد السحب: {str(e)}")
        bot.answer_callback_query(call.id, 
                                "❌ حدث خطأ في المعالجة", 
                                show_alert=True)

def process_withdrawal_refund(call, withdrawal_id):
    """معالجة استرداد طلب السحب"""
    try:
        # التحقق من وجود الطلب وحالته
        withdrawal_data = get_user_pending_withdrawal_from_group(call.from_user.id)
        
        if not withdrawal_data or withdrawal_data['withdrawal_id'] != withdrawal_id:
            bot.answer_callback_query(call.id, 
                                    "❌ الطلب غير موجود أو تم معالجته", 
                                    show_alert=True)
            return
        
        # التحقق إذا كان الطلب تم استرداده مسبقاً
        if is_withdrawal_refunded(withdrawal_id):
            bot.answer_callback_query(call.id, 
                                    "❌ تم استرداد هذا الطلب مسبقاً", 
                                    show_alert=True)
            return
        
        amount = withdrawal_data['amount']
        user_id = withdrawal_data['user_id']
        
        # تحديث حالة الطلب إلى "مسترد"
        success = db_manager.execute_query(
            "UPDATE pending_withdrawals SET status = 'refunded', completed_at = CURRENT_TIMESTAMP WHERE withdrawal_id = %s",
            (withdrawal_id,)
        )
        
        if not success:
            bot.answer_callback_query(call.id, 
                                    "❌ فشل في تحديث حالة الطلب", 
                                    show_alert=True)
            return
        
        # إعادة المبلغ إلى محفظة المستخدم
        current_balance = get_wallet_balance(user_id)
        new_balance = update_wallet_balance(user_id, amount)
        
        # تسجيل معاملة الاسترداد
        transaction_data = {
            'user_id': str(user_id),
            'type': 'refund',
            'amount': amount,
            'description': f'استرداد طلب سحب - رقم الطلب: {withdrawal_id}'
        }
        add_transaction(transaction_data)
        
        # حذف الرسالة من مجموعة الطلبات
        if withdrawal_data['group_message_id'] and withdrawal_data['group_chat_id']:
            delete_group_message(withdrawal_data['group_chat_id'], withdrawal_data['group_message_id'])
        
        # إرسال إشعار للإدارة
        admin_notification = f"""
<b>🔄 استرداد طلب سحب</b>

• المستخدم: <code>{user_id}</code>
• رقم الطلب: <code>{withdrawal_id}</code>
• المبلغ المسترد: <b>{amount:.2f}</b>
• الرصيد السابق: <b>{current_balance:.2f}</b>
• الرصيد الجديد: <b>{new_balance:.2f}</b>
• الوقت: <b>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</b>

✅ تم استرداد الحوالة للمستخدم وحذفها من مجموعة السحب بنجاح.
        """
        
        try:
            bot.send_message(
                ADMIN_CHAT_ID,
                admin_notification,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"خطأ في إرسال إشعار للإدارة: {str(e)}")
        
        # إرسال إشعار للمستخدم
        user_notification = f"""
<b>✅ تم استرداد طلب السحب بنجاح</b>

• المبلغ المسترد: <b>{amount:.2f}</b>
• رصيدك الحالي: <b>{new_balance:.2f}</b>
• رقم العملية: <code>{withdrawal_id}</code>

تم إعادة المبلغ إلى محفظتك بنجاح.
        """
        
        bot.send_message(
            user_id,
            user_notification,
            parse_mode="HTML"
        )
        
        bot.answer_callback_query(call.id, 
                                "✅ تم استرداد طلب السحب بنجاح", 
                                show_alert=True)
        
        # العودة للقائمة الرئيسية
        show_main_menu(user_id, call.message.message_id)
        
    except Exception as e:
        logger.error(f"خطأ في معالجة استرداد السحب: {str(e)}")
        bot.answer_callback_query(call.id, 
                                "❌ حدث خطأ في استرداد الطلب", 
                                show_alert=True)

def add_support_request(user_id, username, message_text=None, photo_id=None):
    """إضافة طلب دعم جديد"""
    request_id = f"support_{int(time.time() * 1000)}"
    
    success = db_manager.execute_query(
        """INSERT INTO support_requests 
        (request_id, user_id, username, message_text, photo_id) 
        VALUES (%s, %s, %s, %s, %s)""",
        (request_id, str(user_id), username, message_text, photo_id)
    )
    
    return request_id if success else None

def get_support_request(request_id):
    """جلب طلب الدعم"""
    result = db_manager.execute_query(
        "SELECT * FROM support_requests WHERE request_id = %s",
        (request_id,)
    )
    
    if result and len(result) > 0:
        row = result[0]
        return {
            'request_id': row['request_id'],
            'user_id': row['user_id'],
            'username': row['username'],
            'message_text': row['message_text'],
            'photo_id': row['photo_id'],
            'status': row['status'],
            'created_at': row['created_at'],
            'admin_chat_id': row['admin_chat_id'],
            'admin_message_id': row['admin_message_id']
        }
    return None

def update_support_admin_message(request_id, admin_chat_id, admin_message_id):
    """تحديث رسالة الإدارة"""
    return db_manager.execute_query(
        """UPDATE support_requests 
        SET admin_chat_id = %s, admin_message_id = %s 
        WHERE request_id = %s""",
        (str(admin_chat_id), admin_message_id, request_id)
    )

def get_pending_support_requests():
    """جلب طلبات الدعم المعلقة"""
    result = db_manager.execute_query(
        "SELECT * FROM support_requests WHERE status = 'pending' ORDER BY created_at DESC"
    )
    return result if result else []

def show_terms_and_conditions(chat_id, message_id=None):
    """عرض الشروط والأحكام"""
    
    terms_text = """
📜 <b>شروط وأحكام استخدام بوت The TATE لإدارة حسابات bets55</b>

1️⃣ <b>عن البوت:</b> بوت رسمي على Telegram لإدارة حسابك في موقع bets55 للمراهنات الإلكترونية بأمان وسرعة.

2️⃣ <b>الخدمات:</b> إنشاء حساب bets55 جديد، إيداع الرصيد، سحب الأرباح، متابعة الرصيد وسجل المعاملات تلقائيًا.

3️⃣ <b>طرق الدفع:</b> Syriatel Cash, Bemo Bank, MTN Cash, USDT (محافظ رقمية), CoinEx Wallet, CWallet, PAYEER USD, ShamCash.

4️⃣ <b>السحب:</b> تحويل الأرباح عبر أي من طرق الدفع السابقة أو أي وسيلة إضافية يدعمها البوت.

5️⃣ <b>نظام الهدايا والمكافآت:</b> أرسل واستقبل نقاط هدية داخل البوت للحصول على مكافآت وعروض حصرية.

6️⃣ <b>الأمان ومكافحة الاحتيال:</b> خوارزميات متطورة لرصد الأنشطة المشبوهة، وحماية بياناتك باستخدام تشفير قوي.

7️⃣ <b>استقلالية الحساب:</b> حسابك منفصل تمامًا ولا يرتبط بأي خدمات أو حسابات خارجية.

8️⃣ <b>المسؤولية:</b> البوت أداة مساعدة لإدارة الحساب فقط، ولا يتحمل خسائرك المالية أو قراراتك الشخصية.

9️⃣ <b>تحديث الشروط:</b> يحق للفريق تعديل الشروط في أي وقت، وسيتم اعلام المستخدم بالتغييرات.

🔟 <b>تعليق وإيقاف الحساب:</b> في حالة مخالفة الشروط، يحق للفريق تعليق أو إنهاء الحساب دون إشعار مسبق.

1️⃣1️⃣ <b>العمر والموافقة:</b> يشترط أن يكون عمر المستخدم 18 سنة فأكثر، وبانضمامك إلى القناة توافق على هذه الشروط.

1️⃣2️⃣ <b>الالتزام باستخدام البوت:</b> يمنع استغلال البوت لأغراض تصريف العملة أو استغلال فروقات سعر الصرف، وأي محاولة من هذا النوع تعرض الحساب للتعليق المباشر.

<b>سياسة الخصوصية:</b> نجمع بيانات الحساب (اسم المستخدم، الرصيد، سجل المعاملات) لتحسين الخدمة وتقديم التنبيهات؛ لا نشاركها مع أي طرف ثالث.
"""
    
    # إنشاء زر الرجوع
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="main_menu"))
    
    try:
        if message_id:
            # تعديل الرسالة الحالية
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=terms_text,
                parse_mode="HTML",
                reply_markup=markup
            )
        else:
            # إرسال رسالة جديدة
            bot.send_message(
                chat_id,
                terms_text,
                parse_mode="HTML",
                reply_markup=markup
            )
    except Exception as e:
        # في حالة الخطأ، إرسال رسالة جديدة
        bot.send_message(
            chat_id,
            terms_text,
            parse_mode="HTML",
            reply_markup=markup
        )

def get_gift_settings():
    """جلب إعدادات نظام الإهداء"""
    result = db_manager.execute_query('SELECT * FROM system_settings WHERE setting_key LIKE %s', ('gift_%',))
    settings = {}
    if result:
        for row in result:
            settings[row['setting_key']] = row['setting_value']
    
    # القيم الافتراضية
    defaults = {
        'gift_commission_rate': '0.1',
        'gift_min_amount': '100',
        'gift_enabled': 'true'
    }
    
    for key, value in defaults.items():
        if key not in settings:
            settings[key] = value
    
    return settings

def save_gift_settings(settings):
    """حفظ إعدادات نظام الإهداء"""
    for key, value in settings.items():
        success = db_manager.execute_query(
            "INSERT INTO system_settings (setting_key, setting_value) VALUES (%s, %s) "
            "ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value",
            (key, str(value))
        )
        if not success:
            return False
    return True

def add_gift_transaction(from_user_id, to_user_id, amount, commission, net_amount):
    """إضافة عملية إهداء جديدة"""
    gift_id = f"gift_{int(time.time() * 1000)}"
    
    success = db_manager.execute_query(
        "INSERT INTO gift_transactions (gift_id, from_user_id, to_user_id, amount, commission, net_amount) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (gift_id, str(from_user_id), str(to_user_id), amount, commission, net_amount)
    )
    
    return gift_id if success else None

def get_user_gift_history(user_id):
    """جلب سجل الإهداء للمستخدم"""
    result = db_manager.execute_query(
        "SELECT * FROM gift_transactions WHERE from_user_id = %s OR to_user_id = %s ORDER BY created_at DESC LIMIT 20",
        (str(user_id), str(user_id))
    )
    return result if result else []

def show_gift_section(chat_id, message_id):
    """عرض قسم الإهداء"""
    settings = get_gift_settings()
    commission_rate = float(settings.get('gift_commission_rate', 0.1)) * 100
    min_amount = float(settings.get('gift_min_amount', 100))
    
    text = f"""
🎁 <b>نظام إهداء الرصيد</b>

<b>معلومات النظام:</b>
• عمولة الإهداء: <b>{commission_rate}%</b>
• الحد الأدنى للإهداء: <b>{min_amount:.2f}</b>
• الآيدي الخاص بك: <code>{chat_id}</code>

<b>كيف يعمل:</b>
1. اختر صديقك وأدخل آيديه
2. أدخل المبلغ المراد إهداؤه
3. تأكد من العملية
4. سيتم خصم المبلغ من رصيدك وإضافته لصديقك

<b>ملاحظة:</b>
سيتم خصم عمولة {commission_rate}% من المبلغ المرسل.
    """
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🎁 بدء عملية الإهداء", callback_data="start_gift"))
    markup.add(types.InlineKeyboardButton("📋 سجل الإهداءات", callback_data="gift_history"))
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="main_menu"))
    
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )
    except:
        bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )

def start_gift_process(chat_id):
    """بدء عملية الإهداء"""
    user_data[chat_id] = {'state': 'gift_user_id'}
    
    text = """
🎁 <b>بدء عملية الإهداء</b>

أهلا بك! يمكنك إرسال هدايا لأصدقائك في أي وقت لتقاسم المتعة والأرباح مع زملائك.

<b>الخطوة 1/2:</b>
يرجى إدخال آيدي صديقك لاستكمال إرسال المبلغ.

<em>ملاحظة: الآيدي يجب أن يكون أرقام فقط ويحتوي على أكثر من 5 أرقام</em>
    """
    
    bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        reply_markup=EnhancedKeyboard.create_back_button("gift_section")
    )

def handle_gift_user_id(message):
    """معالجة إدخال آيدي المستخدم"""
    chat_id = str(message.chat.id)
    user_id = message.text.strip()
    
    # التحقق من صحة الآيدي
    if not user_id.isdigit() or len(user_id) < 6:
        bot.send_message(
            chat_id,
            "❌ <b>آيدي غير صحيح</b>\n\nيرجى إدخال آيدي صحيح (أرقام فقط ويحتوي على أكثر من 5 أرقام)",
            parse_mode="HTML"
        )
        return
    
    # منع الإهداء للنفس
    if user_id == chat_id:
        bot.send_message(
            chat_id,
            "❌ <b>لا يمكنك إهداء الرصيد لنفسك</b>",
            parse_mode="HTML"
        )
        return
    
    # التحقق من وجود المستخدم
    wallet_balance = get_wallet_balance(user_id)
    if wallet_balance == 0 and not db_manager.execute_query(
        "SELECT 1 FROM wallets WHERE chat_id = %s", (user_id,)
    ):
        bot.send_message(
            chat_id,
            "❌ <b>المستخدم غير موجود</b>\n\nيرجى التأكد من الآيدي والمحاولة مرة أخرى",
            parse_mode="HTML"
        )
        return
    
    user_data[chat_id]['gift_user_id'] = user_id
    user_data[chat_id]['state'] = 'gift_amount'
    
    bot.send_message(
        chat_id,
        "✅ <b>تم التحقق من الآيدي بنجاح</b>\n\n<b>الخطوة 2/2:</b>\nيرجى إدخال المبلغ المراد إهداؤه:",
        parse_mode="HTML"
    )

def handle_gift_amount(message):
    """معالجة إدخال المبلغ"""
    chat_id = str(message.chat.id)
    
    try:
        amount = float(message.text.strip())
        settings = get_gift_settings()
        min_amount = float(settings.get('gift_min_amount', 100))
        commission_rate = float(settings.get('gift_commission_rate', 0.1))
        
        # التحقق من الحد الأدنى
        if amount < min_amount:
            bot.send_message(
                chat_id,
                f"❌ <b>المبلغ أقل من الحد الأدنى</b>\n\nالحد الأدنى للإهداء هو: <b>{min_amount:.2f}</b>",
                parse_mode="HTML"
            )
            return
        
        # التحقق من الرصيد
        current_balance = get_wallet_balance(chat_id)
        if current_balance < amount:
            bot.send_message(
                chat_id,
                f"❌ <b>رصيدك غير كافي</b>\n\nرصيدك الحالي: <b>{current_balance:.2f}</b>\nالمبلغ المطلوب: <b>{amount:.2f}</b>",
                parse_mode="HTML"
            )
            return
        
        # حساب العمولة والمبلغ الصافي
        commission = amount * commission_rate
        net_amount = amount - commission
        
        user_data[chat_id]['gift_amount'] = amount
        user_data[chat_id]['gift_commission'] = commission
        user_data[chat_id]['gift_net_amount'] = net_amount
        user_data[chat_id]['state'] = 'gift_confirm'
        
        to_user_id = user_data[chat_id]['gift_user_id']
        
        text = f"""
🎁 <b>تأكيد عملية الإهداء</b>

<b>تفاصيل العملية:</b>
• المرسل: <code>{chat_id}</code>
• المستلم: <code>{to_user_id}</code>
• المبلغ المرسل: <b>{amount:.2f}</b>
• عمولة الإهداء ({commission_rate*100}%): <b>{commission:.2f}</b>
• المبلغ الصافي للمستلم: <b>{net_amount:.2f}</b>

<b>الرصيد بعد العملية:</b>
• رصيدك الجديد: <b>{current_balance - amount:.2f}</b>
• رصيد المستلم الجديد: <b>{get_wallet_balance(to_user_id) + net_amount:.2f}</b>

<b>هل تريد متابعة العملية؟</b>
        """
        
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton("✅ نعم، تأكيد الإهداء", callback_data="confirm_gift"),
            types.InlineKeyboardButton("❌ إلغاء", callback_data="cancel_gift")
        )
        
        bot.send_message(
            chat_id,
            text,
            parse_mode="HTML",
            reply_markup=markup
        )
        
    except ValueError:
        bot.send_message(chat_id, "❌ يرجى إدخال مبلغ صحيح")

def process_gift_transaction(chat_id):
    """معالجة عملية الإهداء"""
    try:
        to_user_id = user_data[chat_id]['gift_user_id']
        amount = user_data[chat_id]['gift_amount']
        commission = user_data[chat_id]['gift_commission']
        net_amount = user_data[chat_id]['gift_net_amount']
        
        # خصم المبلغ من المرسل
        sender_new_balance = update_wallet_balance(chat_id, -amount)
        
        # إضافة المبلغ الصافي للمستلم
        receiver_old_balance = get_wallet_balance(to_user_id)
        receiver_new_balance = update_wallet_balance(to_user_id, net_amount)
        
        # تسجيل العملية
        gift_id = add_gift_transaction(chat_id, to_user_id, amount, commission, net_amount)
        
        # إرسال إشعار للمرسل
        bot.send_message(
            chat_id,
            f"""
✅ <b>تمت عملية الإهداء بنجاح</b>

<b>تفاصيل العملية:</b>
• رقم العملية: <code>{gift_id}</code>
• المبلغ المرسل: <b>{amount:.2f}</b>
• العمولة: <b>{commission:.2f}</b>
• المبلغ المستلم: <b>{net_amount:.2f}</b>
• رصيدك الجديد: <b>{sender_new_balance:.2f}</b>

<b>شكراً لك على المشاركة!</b>
            """,
            parse_mode="HTML"
        )
        
        # إرسال إشعار للمستلم
        try:
            bot.send_message(
                to_user_id,
                f"""
🎁 <b>تهانينا! لقد تلقيت هدية</b>

<b>تفاصيل الهدية:</b>
• المرسل: <code>{chat_id}</code>
• المبلغ: <b>{net_amount:.2f}</b>
• رصيدك السابق: <b>{receiver_old_balance:.2f}</b>
• رصيدك الجديد: <b>{receiver_new_balance:.2f}</b>

<b>استمتع بوقتك!</b>
                """,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"خطأ في إرسال إشعار للمستلم: {str(e)}")
        
        # تنظيف البيانات
        if chat_id in user_data:
            del user_data[chat_id]
            
    except Exception as e:
        logger.error(f"خطأ في معالجة الإهداء: {str(e)}")
        bot.send_message(chat_id, "❌ حدث خطأ أثناء معالجة العملية")

def show_gift_history(chat_id, message_id):
    """عرض سجل الإهداءات"""
    history = get_user_gift_history(chat_id)
    
    text = "<b>📋 سجل عمليات الإهداء</b>\n\n"
    
    if history:
        for i, transaction in enumerate(history, 1):
            if transaction['from_user_id'] == chat_id:
                direction = "🟢 أرسلت"
                other_user = transaction['to_user_id']
            else:
                direction = "🔵 استلمت"
                other_user = transaction['from_user_id']
            
            text += f"""
{direction} إلى <code>{other_user}</code>
• المبلغ: <b>{transaction['amount']:.2f}</b>
• العمولة: <b>{transaction['commission']:.2f}</b>
• الصافي: <b>{transaction['net_amount']:.2f}</b>
• التاريخ: {transaction['created_at'].strftime('%Y-%m-%d %H:%M')}
────────────────────
            """
    else:
        text += "❌ <b>لا توجد عمليات إهداء سابقة</b>"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="gift_section"))
    
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )
    except:
        bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )

def get_gift_stats():
    """جلب إحصائيات نظام الإهداء"""
    try:
        # إجمالي عمليات الإهداء
        total_result = db_manager.execute_query(
            "SELECT COUNT(*) as total_count, COALESCE(SUM(amount), 0) as total_amount FROM gift_transactions"
        )
        
        # عمليات اليوم
        today_result = db_manager.execute_query(
            "SELECT COUNT(*) as today_count, COALESCE(SUM(amount), 0) as today_amount FROM gift_transactions WHERE created_at >= CURRENT_DATE"
        )
        
        # إجمالي العمولة
        commission_result = db_manager.execute_query(
            "SELECT COALESCE(SUM(commission), 0) as total_commission FROM gift_transactions"
        )
        
        # أفضل 5 مرسلين
        top_senders = db_manager.execute_query(
            "SELECT from_user_id, COUNT(*) as gift_count, SUM(amount) as total_sent FROM gift_transactions GROUP BY from_user_id ORDER BY total_sent DESC LIMIT 5"
        )
        
        # أفضل 5 مستقبلين
        top_receivers = db_manager.execute_query(
            "SELECT to_user_id, COUNT(*) as gift_count, SUM(net_amount) as total_received FROM gift_transactions GROUP BY to_user_id ORDER BY total_received DESC LIMIT 5"
        )
        
        stats = {
            'total_count': total_result[0]['total_count'] if total_result else 0,
            'total_amount': float(total_result[0]['total_amount']) if total_result else 0,
            'today_count': today_result[0]['today_count'] if today_result else 0,
            'today_amount': float(today_result[0]['today_amount']) if today_result else 0,
            'total_commission': float(commission_result[0]['total_commission']) if commission_result else 0,
            'top_senders': top_senders if top_senders else [],
            'top_receivers': top_receivers if top_receivers else []
        }
        
        return stats
        
    except Exception as e:
        logger.error(f"خطأ في جلب إحصائيات الإهداء: {str(e)}")
        return {
            'total_count': 0, 'total_amount': 0, 'today_count': 0, 
            'today_amount': 0, 'total_commission': 0,
            'top_senders': [], 'top_receivers': []
        }

def get_all_gift_transactions(limit=50):
    """جلب جميع عمليات الإهداء"""
    try:
        result = db_manager.execute_query(
            "SELECT * FROM gift_transactions ORDER BY created_at DESC LIMIT %s",
            (limit,)
        )
        return result if result else []
    except Exception as e:
        logger.error(f"خطأ في جلب عمليات الإهداء: {str(e)}")
        return []

def update_gift_settings(commission_rate=None, min_amount=None, enabled=None):
    """تحديث إعدادات الإهداء"""
    try:
        settings = get_gift_settings()
        
        if commission_rate is not None:
            settings['gift_commission_rate'] = str(commission_rate)
        if min_amount is not None:
            settings['gift_min_amount'] = str(min_amount)
        if enabled is not None:
            settings['gift_enabled'] = 'true' if enabled else 'false'
        
        return save_gift_settings(settings)
        
    except Exception as e:
        logger.error(f"خطأ في تحديث إعدادات الإهداء: {str(e)}")
        return False
def show_gift_admin_panel(chat_id, message_id):
    """عرض لوحة إدارة الإهداء"""
    if not is_admin(chat_id):
        bot.answer_callback_query(chat_id, "ليس لديك صلاحية الدخول", show_alert=True)
        return
    
    settings = get_gift_settings()
    stats = get_gift_stats()
    
    commission_rate = float(settings.get('gift_commission_rate', 0.1)) * 100
    min_amount = float(settings.get('gift_min_amount', 100))
    enabled = settings.get('gift_enabled', 'true') == 'true'
    
    text = f"""
🎁 <b>لوحة إدارة نظام الإهداء</b>

<b>📊 الإحصائيات:</b>
• إجمالي العمليات: <b>{stats['total_count']}</b>
• إجمالي المبالغ: <b>{stats['total_amount']:.2f}</b>
• عمليات اليوم: <b>{stats['today_count']}</b>
• مبالغ اليوم: <b>{stats['today_amount']:.2f}</b>
• إجمالي العمولة: <b>{stats['total_commission']:.2f}</b>

<b>⚙️ الإعدادات الحالية:</b>
• نسبة العمولة: <b>{commission_rate}%</b>
• الحد الأدنى: <b>{min_amount:.2f}</b>
• حالة النظام: <b>{'✅ مفعل' if enabled else '❌ معطل'}</b>

<b>👥 أفضل المرسلين:</b>
"""
    
    if stats['top_senders']:
        for i, sender in enumerate(stats['top_senders'], 1):
            text += f"{i}. {sender['from_user_id'][:8]}... - {sender['total_sent']:.2f} ({sender['gift_count']} عملية)\n"
    else:
        text += "لا توجد بيانات\n"
    
    text += "\n<b>🎯 اختر الإجراء المطلوب:</b>"
    
    markup = types.InlineKeyboardMarkup()
    
    markup.row(
        types.InlineKeyboardButton("📈 إحصائيات مفصلة", callback_data="gift_detailed_stats"),
        types.InlineKeyboardButton("⚙️ تعديل الإعدادات", callback_data="edit_gift_settings")
    )
    
    markup.row(
        types.InlineKeyboardButton("📋 جميع العمليات", callback_data="all_gift_transactions")
        
    )
    
    markup.row(
        types.InlineKeyboardButton("🔄 تحديث", callback_data="gift_admin")
        
    )
    
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel"))
    
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )
    except:
        bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )

def show_gift_detailed_stats(chat_id, message_id):
    """عرض إحصائيات مفصلة للإهداء"""
    if not is_admin(chat_id):
        bot.answer_callback_query(chat_id, "ليس لديك صلاحية الدخول", show_alert=True)
        return
    
    stats = get_gift_stats()
    
    text = f"""
📈 <b>إحصائيات مفصلة - نظام الإهداء</b>

<b>📊 الإحصائيات العامة:</b>
• إجمالي العمليات: <b>{stats['total_count']}</b>
• إجمالي المبالغ: <b>{stats['total_amount']:.2f}</b>
• عمليات اليوم: <b>{stats['today_count']}</b>
• مبالغ اليوم: <b>{stats['today_amount']:.2f}</b>
• إجمالي العمولة: <b>{stats['total_commission']:.2f}</b>
• متوسط المبلغ: <b>{stats['total_amount']/max(stats['total_count'], 1):.2f}</b>

<b>🏆 أفضل 5 مرسلين:</b>
"""
    
    if stats['top_senders']:
        for i, sender in enumerate(stats['top_senders'], 1):
            text += f"{i}. <code>{sender['from_user_id']}</code>\n"
            text += f"   • الإجمالي: <b>{sender['total_sent']:.2f}</b>\n"
            text += f"   • عدد العمليات: <b>{sender['gift_count']}</b>\n"
            text += f"   • المتوسط: <b>{sender['total_sent']/sender['gift_count']:.2f}</b>\n\n"
    else:
        text += "لا توجد بيانات\n\n"
    
    text += "<b>🎯 أفضل 5 مستقبلين:</b>\n"
    
    if stats['top_receivers']:
        for i, receiver in enumerate(stats['top_receivers'], 1):
            text += f"{i}. <code>{receiver['to_user_id']}</code>\n"
            text += f"   • الإجمالي: <b>{receiver['total_received']:.2f}</b>\n"
            text += f"   • عدد العمليات: <b>{receiver['gift_count']}</b>\n\n"
    else:
        text += "لا توجد بيانات\n"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="gift_admin"))
    
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )
    except:
        bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )

def show_all_gift_transactions(chat_id, message_id):
    """عرض جميع عمليات الإهداء"""
    if not is_admin(chat_id):
        bot.answer_callback_query(chat_id, "ليس لديك صلاحية الدخول", show_alert=True)
        return
    
    transactions = get_all_gift_transactions(30)
    
    text = "📋 <b>آخر 30 عملية إهداء</b>\n\n"
    
    if transactions:
        for i, transaction in enumerate(transactions, 1):
            text += f"<b>عملية #{i}</b>\n"
            text += f"• الرقم: <code>{transaction['gift_id']}</code>\n"
            text += f"• المرسل: <code>{transaction['from_user_id']}</code>\n"
            text += f"• المستلم: <code>{transaction['to_user_id']}</code>\n"
            text += f"• المبلغ: <b>{transaction['amount']:.2f}</b>\n"
            text += f"• العمولة: <b>{transaction['commission']:.2f}</b>\n"
            text += f"• الصافي: <b>{transaction['net_amount']:.2f}</b>\n"
            text += f"• التاريخ: {transaction['created_at'].strftime('%Y-%m-%d %H:%M')}\n"
            text += "────────────────────\n"
    else:
        text += "❌ <b>لا توجد عمليات إهداء</b>"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="gift_admin"))
    
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )
    except:
        bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )

def show_edit_gift_settings(chat_id, message_id):
    """عرض واجهة تعديل إعدادات الإهداء"""
    if not is_admin(chat_id):
        bot.answer_callback_query(chat_id, "ليس لديك صلاحية الدخول", show_alert=True)
        return
    
    settings = get_gift_settings()
    commission_rate = float(settings.get('gift_commission_rate', 0.1)) * 100
    min_amount = float(settings.get('gift_min_amount', 100))
    
    text = f"""
⚙️ <b>تعديل إعدادات الإهداء</b>

<b>الإعدادات الحالية:</b>
• نسبة العمولة: <b>{commission_rate}%</b>
• الحد الأدنى للإهداء: <b>{min_amount:.2f}</b>

<b>اختر الإعداد الذي تريد تعديله:</b>
"""
    
    markup = types.InlineKeyboardMarkup()
    
    markup.row(
        types.InlineKeyboardButton("📊 تعديل نسبة العمولة", callback_data="edit_gift_commission"),
        types.InlineKeyboardButton("💰 تعديل الحد الأدنى", callback_data="edit_gift_min_amount")
    )
    
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="gift_admin"))
    
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )
    except:
        bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )

def start_edit_gift_commission(chat_id):
    """بدء تعديل نسبة عمولة الإهداء"""
    if not is_admin(chat_id):
        return
    
    user_data[chat_id] = {'state': 'edit_gift_commission'}
    
    settings = get_gift_settings()
    current_rate = float(settings.get('gift_commission_rate', 0.1)) * 100
    
    bot.send_message(
        chat_id,
        f"📊 <b>تعديل نسبة عمولة الإهداء</b>\n\n"
        f"النسبة الحالية: <b>{current_rate}%</b>\n\n"
        f"أرسل النسبة الجديدة (0 - 50):\n"
        f"<em>مثال: 10 ← لنسبة 10%</em>",
        parse_mode="HTML",
        reply_markup=EnhancedKeyboard.create_back_button("edit_gift_settings")
    )

def start_edit_gift_min_amount(chat_id):
    """بدء تعديل الحد الأدنى للإهداء"""
    if not is_admin(chat_id):
        return
    
    user_data[chat_id] = {'state': 'edit_gift_min_amount'}
    
    settings = get_gift_settings()
    current_min = float(settings.get('gift_min_amount', 100))
    
    bot.send_message(
        chat_id,
        f"💰 <b>تعديل الحد الأدنى للإهداء</b>\n\n"
        f"القيمة الحالية: <b>{current_min:.2f}</b>\n\n"
        f"أرسل القيمة الجديدة:\n"
        f"<em>مثال: 500 ← للحد الأدنى 500</em>",
        parse_mode="HTML",
        reply_markup=EnhancedKeyboard.create_back_button("edit_gift_settings")
    )

def handle_edit_gift_commission(message):
    """معالجة تعديل نسبة العمولة"""
    chat_id = str(message.chat.id)
    
    try:
        commission_percent = float(message.text.strip())
        
        if commission_percent < 0 or commission_percent > 50:
            bot.send_message(chat_id, "❌ النسبة يجب أن تكون بين 0 و 50")
            return
        
        commission_rate = commission_percent / 100
        
        if update_gift_settings(commission_rate=commission_rate):
            bot.send_message(
                chat_id,
                f"✅ <b>تم تحديث نسبة العمولة إلى {commission_percent}%</b>",
                parse_mode="HTML"
            )
        else:
            bot.send_message(chat_id, "❌ فشل في تحديث الإعدادات")
        
        # تنظيف البيانات والعودة
        if chat_id in user_data:
            del user_data[chat_id]
        
        show_edit_gift_settings(chat_id, None)
        
    except ValueError:
        bot.send_message(chat_id, "❌ يرجى إدخال رقم صحيح")

def handle_edit_gift_min_amount(message):
    """معالجة تعديل الحد الأدنى"""
    chat_id = str(message.chat.id)
    
    try:
        min_amount = float(message.text.strip())
        
        if min_amount < 1:
            bot.send_message(chat_id, "❌ القيمة يجب أن تكون أكبر من 0")
            return
        
        if update_gift_settings(min_amount=min_amount):
            bot.send_message(
                chat_id,
                f"✅ <b>تم تحديث الحد الأدنى إلى {min_amount:.2f}</b>",
                parse_mode="HTML"
            )
        else:
            bot.send_message(chat_id, "❌ فشل في تحديث الإعدادات")
        
        # تنظيف البيانات والعودة
        if chat_id in user_data:
            del user_data[chat_id]
        
        show_edit_gift_settings(chat_id, None)
        
    except ValueError:
        bot.send_message(chat_id, "❌ يرجى إدخال رقم صحيح")
def export_gift_data(chat_id):
    """تصدير بيانات الإهداء"""
    if not is_admin(chat_id):
        return
    
    try:
        transactions = get_all_gift_transactions(1000)  # جلب أكبر عدد ممكن
        
        if transactions:
            # إنشاء ملف CSV
            csv_data = "Gift ID,From User,To User,Amount,Commission,Net Amount,Date\n"
            
            for transaction in transactions:
                csv_data += f"{transaction['gift_id']},{transaction['from_user_id']},{transaction['to_user_id']},"
                csv_data += f"{transaction['amount']},{transaction['commission']},{transaction['net_amount']},"
                csv_data += f"{transaction['created_at'].strftime('%Y-%m-%d %H:%M:%S')}\n"
            
            # إرسال الملف
            bot.send_document(
                chat_id,
                ('gift_transactions.csv', csv_data.encode('utf-8')),
                caption="<b>📤 تصدير بيانات الإهداء</b>\n\nتم تصدير آخر 1000 عملية إهداء",
                parse_mode="HTML"
            )
        else:
            bot.send_message(chat_id, "❌ لا توجد بيانات للتصدير")
            
    except Exception as e:
        logger.error(f"خطأ في تصدير بيانات الإهداء: {str(e)}")
        bot.send_message(chat_id, "❌ حدث خطأ أثناء التصدير")

def toggle_gift_system(chat_id, message_id):
    """تفعيل/تعطيل نظام الإهداء"""
    if not is_admin(chat_id):
        bot.answer_callback_query(chat_id, "ليس لديك صلاحية الدخول", show_alert=True)
        return
    
    settings = get_gift_settings()
    current_status = settings.get('gift_enabled', 'true') == 'true'
    new_status = not current_status
    
    if update_gift_settings(enabled=new_status):
        status_text = "مفعل" if new_status else "معطل"
        bot.answer_callback_query(chat_id, f"تم {status_text} نظام الإهداء")
        show_gift_admin_panel(chat_id, message_id)
    else:
        bot.answer_callback_query(chat_id, "فشل في تحديث الإعدادات", show_alert=True)

def can_user_use_gift_code_today(user_id):
    """التحقق إذا كان المستخدم يمكنه استخدام أي كود هدية اليوم"""
    try:
        result = db_manager.execute_query(
            """SELECT 1 FROM gift_code_usage 
               WHERE user_id = %s AND used_at >= NOW() - INTERVAL '24 hours'""",
            (str(user_id),)
        )
        return not (result and len(result) > 0)
    except Exception as e:
        logger.error(f"خطأ في التحقق من استخدام الكود اليوم: {str(e)}")
        return False

def use_gift_code(code, user_id):
    """استخدام كود هدية"""
    try:
        # التحقق إذا استخدم المستخدم أي كود خلال 24 ساعة
        if not can_user_use_gift_code_today(user_id):
            return False, "⚠️ يمكنك استخدام كود هدية واحدة فقط كل 24 ساعة"
        
        # التحقق من أن المستخدم لم يستخدم هذا الكود من قبل
        existing_usage = db_manager.execute_query(
            "SELECT 1 FROM gift_code_usage WHERE code = %s AND user_id = %s",
            (code.upper(), str(user_id))
        )
        
        if existing_usage and len(existing_usage) > 0:
            return False, "❌ لقد استخدمت هذا الكود من قبل"
        
        # التحقق من صلاحية الكود
        code_data = db_manager.execute_query(
            """SELECT code, amount, max_uses, used_count, expires_at, active 
               FROM gift_codes WHERE code = %s""",
            (code.upper(),)
        )
        
        if not code_data or len(code_data) == 0:
            return False, "❌ الكود غير صحيح"
        
        code_info = code_data[0]
        
        if not code_info['active']:
            return False, "❌ الكود غير فعال"
        
        if code_info['expires_at'] and code_info['expires_at'] < datetime.now():
            return False, "❌ الكود منتهي الصلاحية"
        
        if code_info['used_count'] >= code_info['max_uses']:
            return False, "❌ تم استخدام هذا الكود بالكامل"
        
        # استخدام الكود
        success = db_manager.execute_query(
            "UPDATE gift_codes SET used_count = used_count + 1 WHERE code = %s",
            (code.upper(),)
        )
        
        if success:
            db_manager.execute_query(
                """INSERT INTO gift_code_usage (code, user_id, amount_received) 
                   VALUES (%s, %s, %s)""",
                (code.upper(), str(user_id), code_info['amount'])
            )
            
            # إضافة المبلغ للمحفظة
            new_balance = update_wallet_balance(user_id, code_info['amount'])
            
            # تسجيل المعاملة
            add_transaction({
                'user_id': str(user_id),
                'type': 'gift_code',
                'amount': code_info['amount'],
                'description': f"هدية من كود: {code.upper()}"
            })
            
            return True, f"🎉 تمت إضافة {code_info['amount']} إلى رصيدك بنجاح!"
        
        return False, "❌ حدث خطأ أثناء معالجة الكود"
        
    except Exception as e:
        logger.error(f"خطأ في استخدام كود الهدية: {str(e)}")
        return False, "❌ حدث خطأ أثناء معالجة الكود"

def create_gift_code(code, amount, max_uses, created_by, expires_hours=24):
    """إنشاء كود هدية جديد"""
    try:
        expires_at = datetime.now() + timedelta(hours=expires_hours) if expires_hours > 0 else None
        
        success = db_manager.execute_query(
            """INSERT INTO gift_codes (code, amount, max_uses, created_by, expires_at) 
               VALUES (%s, %s, %s, %s, %s)""",
            (code.upper(), amount, max_uses, created_by, expires_at)
        )
        return success
    except Exception as e:
        logger.error(f"خطأ في إنشاء كود الهدية: {str(e)}")
        return False

def start_gift_code_input(chat_id):
    """بدء إدخال كود الهدية"""
    # التحقق أولاً إذا يمكن استخدام كود اليوم
    if not can_user_use_gift_code_today(chat_id):
        bot.send_message(
            chat_id,
            "❌ لقد استخدمت كود هدية اليوم بالفعل\n\n⏰ يمكنك استخدام كود جديد بعد 24 ساعة من آخر استخدام",
            parse_mode="HTML"
        )
        return
    
    user_data[chat_id] = {'state': 'gift_code_input'}
    
    bot.send_message(
        chat_id,
        "🎟 كود هدية\n\nأدخل الكود للحصول على الهدية\n\nيتم الحصول على الهدية من خلال صفحتنا على الفيسبوك وقناتنا التلغرام",
        parse_mode="HTML"
    )

def start_create_gift_code(chat_id):
    """بدء إنشاء كود هدية (للآدمن)"""
    user_data[chat_id] = {'state': 'create_gift_code'}
    
    bot.send_message(
        chat_id,
        "🛠 إنشاء كود هدية جديد\n\nأدخل الكود (مثال: WELCOME2024):",
        parse_mode="HTML"
    )
@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and 
                    user_data[str(message.chat.id)].get('state') == 'gift_code_input')
def handle_gift_code_input(message):
    """معالجة إدخال كود الهدية"""
    chat_id = str(message.chat.id)
    code = message.text.strip().upper()
    
    if not code:
        bot.send_message(chat_id, "❌ يرجى إدخال كود صحيح")
        return
    
    # استخدام الكود
    success, message_text = use_gift_code(code, chat_id)
    
    # تنظيف البيانات
    if chat_id in user_data:
        del user_data[chat_id]
    
    bot.send_message(chat_id, message_text, parse_mode="HTML")

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and 
                    user_data[str(message.chat.id)].get('state') == 'create_gift_code')
def handle_create_gift_code(message):
    """معالجة إنشاء كود هدية"""
    chat_id = str(message.chat.id)
    code = message.text.strip().upper()
    
    if not code or len(code) < 3:
        bot.send_message(chat_id, "❌ يرجى إدخال كود صحيح (3 أحرف على الأقل)")
        return
    
    user_data[chat_id]['gift_code'] = code
    user_data[chat_id]['state'] = 'create_gift_code_amount'
    
    bot.send_message(chat_id, "💰 أدخل مبلغ الهدية:")

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and 
                    user_data[str(message.chat.id)].get('state') == 'create_gift_code_amount')
def handle_create_gift_code_amount(message):
    """معالجة مبلغ كود الهدية"""
    chat_id = str(message.chat.id)
    
    try:
        amount = float(message.text.strip())
        
        if amount <= 0:
            bot.send_message(chat_id, "❌ المبلغ يجب أن يكون أكبر من الصفر")
            return
        
        user_data[chat_id]['gift_code_amount'] = amount
        user_data[chat_id]['state'] = 'create_gift_code_uses'
        
        bot.send_message(chat_id, "🔢 أدخل عدد مرات الاستخدام المسموحة:")
        
    except ValueError:
        bot.send_message(chat_id, "❌ يرجى إدخال مبلغ صحيح")

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and 
                    user_data[str(message.chat.id)].get('state') == 'create_gift_code_uses')
def handle_create_gift_code_uses(message):
    """معالجة عدد استخدمات كود الهدية"""
    chat_id = str(message.chat.id)
    
    try:
        max_uses = int(message.text.strip())
        
        if max_uses <= 0:
            bot.send_message(chat_id, "❌ عدد الاستخدامات يجب أن يكون أكبر من الصفر")
            return
        
        code = user_data[chat_id]['gift_code']
        amount = user_data[chat_id]['gift_code_amount']
        
        # إنشاء الكود
        success = create_gift_code(code, amount, max_uses, str(chat_id))
        
        # تنظيف البيانات
        if chat_id in user_data:
            del user_data[chat_id]
        
        if success:
            bot.send_message(
                chat_id,
                f"✅ تم إنشاء كود الهدية بنجاح\n\n"
                f"🎟 الكود: <code>{code}</code>\n"
                f"💰 المبلغ: {amount}\n"
                f"🔢 عدد الاستخدامات: {max_uses}",
                parse_mode="HTML"
            )
        else:
            bot.send_message(chat_id, "❌ فشل في إنشاء الكود، قد يكون الكود مستخدم مسبقاً")
        
    except ValueError:
        bot.send_message(chat_id, "❌ يرجى إدخال عدد صحيح")
# ===============================================================
# نظام سجل السحوبات - دوال مستقلة
# ===============================================================

def get_user_withdraw_history(user_id, limit=20):
    """جلب سجل السحوبات للمستخدم من قاعدة البيانات"""
    try:
        result = db_manager.execute_query(
            "SELECT * FROM pending_withdrawals WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
            (str(user_id), limit)
        )
        return result if result else []
    except Exception as e:
        logger.error(f"خطأ في جلب سجل السحوبات: {str(e)}")
        return []

def get_all_user_withdrawals(user_id):
    """جلب جميع سحوبات المستخدم بجميع حالاتها"""
    try:
        result = db_manager.execute_query(
            "SELECT * FROM pending_withdrawals WHERE user_id = %s ORDER BY created_at DESC",
            (str(user_id),)
        )
        return result if result else []
    except Exception as e:
        logger.error(f"خطأ في جلب جميع السحوبات: {str(e)}")
        return []

def format_withdraw_status(status):
    """تنسيق حالة السحب"""
    status_map = {
        'pending': '⏳ قيد الانتظار',
        'completed': '✅ مكتمل',
        'refunded': '🔄 مسترد',
        'rejected': '❌ مرفوض'
    }
    return status_map.get(status, status)

def format_withdraw_history_text(withdrawals):
    """تنسيق نص سجل السحوبات"""
    if not withdrawals:
        return "❌ لا توجد عمليات سحب سابقة"
    
    text = "📋 <b>سجل عمليات السحب</b>\n\n"
    
    for i, withdrawal in enumerate(withdrawals, 1):
        method_id = withdrawal['method_id']
        method_name = withdraw_system.methods.get(method_id, {}).get('name', 'غير معروف')
        amount = withdrawal['amount']
        status = format_withdraw_status(withdrawal['status'])
        date = withdrawal['created_at'].strftime('%Y-%m-%d %H:%M')
        
        text += f"<b>عملية #{i}</b>\n"
        text += f"💳 <b>الطريقة:</b> {method_name}\n"
        text += f"💰 <b>المبلغ:</b> {float(amount):.2f}\n"
        text += f"📮 <b>الحالة:</b> {status}\n"
        text += f"📅 <b>التاريخ:</b> {date}\n"
        
        if withdrawal.get('completed_at'):
            completed_date = withdrawal['completed_at'].strftime('%Y-%m-%d %H:%M')
            text += f"⏱️ <b>وقت الإكمال:</b> {completed_date}\n"
        
        text += "─" * 20 + "\n"
    
    return text

def show_withdraw_history(chat_id, message_id=None):
    """عرض سجل السحوبات للمستخدم"""
    try:
        # جلب سجل السحوبات
        withdrawals = get_user_withdraw_history(chat_id, 15)
        
        # تنسيق النص
        text = format_withdraw_history_text(withdrawals)
        
        # إنشاء أزرار التحكم
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton("🔄 تحديث", callback_data="withdraw_history"),
            types.InlineKeyboardButton("📊 إحصائيات", callback_data="withdraw_stats")
        )
        markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="main_menu"))
        
        # إرسال أو تعديل الرسالة
        if message_id:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode="HTML",
                reply_markup=markup
            )
        else:
            bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=markup
            )
            
    except Exception as e:
        logger.error(f"خطأ في عرض سجل السحوبات: {str(e)}")
        error_text = "❌ حدث خطأ في جلب سجل السحوبات. يرجى المحاولة لاحقاً."
        if message_id:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=error_text,
                parse_mode="HTML"
            )
        else:
            bot.send_message(chat_id, error_text, parse_mode="HTML")

def show_withdraw_stats(chat_id, message_id):
    """عرض إحصائيات السحوبات للمستخدم"""
    try:
        # جلب جميع سحوبات المستخدم
        all_withdrawals = get_all_user_withdrawals(chat_id)
        
        if not all_withdrawals:
            text = "📊 <b>إحصائيات السحوبات</b>\n\n❌ لا توجد عمليات سحب سابقة"
        else:
            # حساب الإحصائيات
            total_withdrawals = len(all_withdrawals)
            total_amount = sum(float(w['amount']) for w in all_withdrawals)
            completed_count = len([w for w in all_withdrawals if w['status'] == 'completed'])
            pending_count = len([w for w in all_withdrawals if w['status'] == 'pending'])
            refunded_count = len([w for w in all_withdrawals if w['status'] == 'refunded'])
            
            # أول وآخر سحب
            first_withdraw = min(all_withdrawals, key=lambda x: x['created_at'])
            last_withdraw = max(all_withdrawals, key=lambda x: x['created_at'])
            
            text = "📊 <b>إحصائيات السحوبات</b>\n\n"
            text += f"📈 <b>إجمالي العمليات:</b> {total_withdrawals}\n"
            text += f"💰 <b>إجمالي المبالغ:</b> {total_amount:.2f}\n"
            text += f"✅ <b>العمليات المكتملة:</b> {completed_count}\n"
            text += f"⏳ <b>العمليات المعلقة:</b> {pending_count}\n"
            text += f"🔄 <b>العمليات المستردة:</b> {refunded_count}\n"
            text += f"📅 <b>أول عملية:</b> {first_withdraw['created_at'].strftime('%Y-%m-%d')}\n"
            text += f"📅 <b>آخر عملية:</b> {last_withdraw['created_at'].strftime('%Y-%m-%d')}\n"
            
            if completed_count > 0:
                avg_amount = total_amount / completed_count
                text += f"📊 <b>متوسط المبلغ:</b> {avg_amount:.2f}\n"
        
        # أزرار التحكم
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton("📋 السجل الكامل", callback_data="withdraw_history"),
            types.InlineKeyboardButton("🔄 تحديث", callback_data="withdraw_stats")
        )
        markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="main_menu"))
        
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )
        
    except Exception as e:
        logger.error(f"خطأ في عرض إحصائيات السحوبات: {str(e)}")
        bot.answer_callback_query(
            chat_id,
            "❌ حدث خطأ في جلب الإحصائيات",
            show_alert=True
        )

def export_withdraw_history(user_id):
    """تصدير سجل السحوبات كملف CSV (للمستخدمين المتقدمين)"""
    try:
        withdrawals = get_all_user_withdrawals(user_id)
        
        if not withdrawals:
            return False, "لا توجد بيانات للتصدير"
        
        # إنشاء ملف CSV
        csv_data = "رقم العملية,الطريقة,المبلغ,الحالة,التاريخ,وقت الإكمال\n"
        
        for withdrawal in withdrawals:
            method_id = withdrawal['method_id']
            method_name = withdraw_system.methods.get(method_id, {}).get('name', 'غير معروف')
            
            csv_data += f"{withdrawal['withdrawal_id']},{method_name},{withdrawal['amount']},"
            csv_data += f"{withdrawal['status']},{withdrawal['created_at'].strftime('%Y-%m-%d %H:%M')},"
            csv_data += f"{withdrawal['completed_at'].strftime('%Y-%m-%d %H:%M') if withdrawal['completed_at'] else 'N/A'}\n"
        
        return True, csv_data
        
    except Exception as e:
        logger.error(f"خطأ في تصدير سجل السحوبات: {str(e)}")
        return False, "حدث خطأ أثناء التصدير"

def search_withdrawals_by_date(user_id, start_date, end_date):
    """بحث في السحوبات حسب التاريخ"""
    try:
        result = db_manager.execute_query(
            "SELECT * FROM pending_withdrawals WHERE user_id = %s AND created_at BETWEEN %s AND %s ORDER BY created_at DESC",
            (str(user_id), start_date, end_date)
        )
        return result if result else []
    except Exception as e:
        logger.error(f"خطأ في البحث في السحوبات: {str(e)}")
        return []

# ===============================================================
# دوال نظام الإحالات
# ===============================================================

def save_referral_settings(settings):
    """حفظ إعدادات الإحالات"""
    for key, value in settings.items():
        success = db_manager.execute_query(
            "INSERT INTO referral_settings (setting_key, setting_value) VALUES (%s, %s) "
            "ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value, updated_at = CURRENT_TIMESTAMP",
            (key, str(value))
        )
        if not success:
            return False
    return True

def load_referral_settings():
    """تحميل إعدادات الإحالات"""
    result = db_manager.execute_query("SELECT * FROM referral_settings")
    settings = {}
    if result:
        for row in result:
            settings[row['setting_key']] = row['setting_value']
    
    # القيم الافتراضية إذا لم تكن موجودة
    if 'commission_rate' not in settings:
        settings['commission_rate'] = '0.1'
    if 'payout_days' not in settings:
        settings['payout_days'] = '10'
    if 'last_payout_date' not in settings:
        settings['last_payout_date'] = datetime.now().isoformat()
    if 'next_payout_date' not in settings:
        next_payout = datetime.now() + timedelta(days=10)
        settings['next_payout_date'] = next_payout.isoformat()
        
    return settings

def add_referral(referrer_id, referred_id):
    """إضافة إحالة جديدة"""
    success = db_manager.execute_query(
        "INSERT INTO referrals (referrer_id, referred_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (str(referrer_id), str(referred_id))
    )
    
    if success:
        # إضافة نقطة للمحيل
        settings = load_loyalty_settings()
        referral_points = int(settings.get('referral_points', 1))
        add_loyalty_points(referrer_id, referral_points, "نقطة إحالة جديدة")
    
    return success

def get_referrer(referred_id):
    """جلب المحيل للمستخدم"""
    result = db_manager.execute_query(
        "SELECT referrer_id FROM referrals WHERE referred_id = %s",
        (str(referred_id),)
    )
    if result and len(result) > 0:
        return result[0]['referrer_id']
    return None

def log_referral_commission(referrer_id, referred_id, transaction_type, amount, net_loss=0, commission_amount=None):
    """تسجيل عمولة الإحالة"""
    settings = load_referral_settings()
    commission_rate = float(settings.get('commission_rate', 0.1))
    
    if commission_amount is None:
        if transaction_type == 'deposit':
            commission_amount = amount * commission_rate
        else:
            commission_amount = net_loss * commission_rate

    return db_manager.execute_query(
        "INSERT INTO referral_commissions (referrer_id, referred_id, transaction_type, "
        "amount, net_loss, commission_rate, commission_amount) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (str(referrer_id), str(referred_id), transaction_type, amount, net_loss, commission_rate, commission_amount)
    )

def update_referral_earning(referrer_id, amount):
    """تحديث مستحقات المحيل"""
    return db_manager.execute_query(
        "INSERT INTO referral_earnings (referrer_id, pending_commission) VALUES (%s, %s) "
        "ON CONFLICT (referrer_id) DO UPDATE SET pending_commission = referral_earnings.pending_commission + EXCLUDED.pending_commission",
        (str(referrer_id), amount)
    )

def get_pending_commissions():
    """جلب المستحقات المعلقة"""
    result = db_manager.execute_query(
        "SELECT referrer_id, SUM(pending_commission) as total_pending FROM referral_earnings GROUP BY referrer_id HAVING SUM(pending_commission) > 0"
    )
    return result if result else []

def reset_pending_commissions():
    """إعادة تعيين المستحقات المعلقة"""
    return db_manager.execute_query(
        "UPDATE referral_earnings SET pending_commission = 0, total_commission = total_commission + pending_commission"
    )

def get_referral_stats(referrer_id):
    """جلب إحصائيات المحيل"""
    result = db_manager.execute_query(
        "SELECT COUNT(*) as referral_count FROM referrals WHERE referrer_id = %s",
        (str(referrer_id),)
    )
    referral_count = result[0]['referral_count'] if result else 0
    
    result = db_manager.execute_query(
        "SELECT COALESCE(SUM(pending_commission), 0) as pending, COALESCE(SUM(total_commission), 0) as total FROM referral_earnings WHERE referrer_id = %s",
        (str(referrer_id),)
    )
    pending = result[0]['pending'] if result else 0
    total = result[0]['total'] if result else 0
    
    return {
        'referral_count': referral_count,
        'pending_commission': float(pending),
        'total_commission': float(total)
    }

def get_user_referrals(referrer_id):
    """جلب إحالات المستخدم"""
    result = db_manager.execute_query(
        "SELECT referred_id, created_at FROM referrals WHERE referrer_id = %s ORDER BY created_at DESC",
        (str(referrer_id),)
    )
    return result if result else []

def check_payout_time():
    """التحقق من موعد توزيع العمولات"""
    settings = load_referral_settings()
    next_payout_str = settings.get('next_payout_date')
    if next_payout_str:
        try:
            next_payout = datetime.fromisoformat(next_payout_str)
            return datetime.now() >= next_payout
        except:
            pass
    return False

def send_payout_notification():
    """إرسال إشعار توزيع العمولات للإدارة"""
    if not check_payout_time():
        return
    
    pending_commissions = get_pending_commissions()
    total_pending = sum(commission['total_pending'] for commission in pending_commissions)
    
    text = f"""
⚠️ <b>إشعار توزيع عمولات الإحالات</b>

⏰ <b>حان موعد توزيع عمولات الإحالات</b>

📊 <b>الإحصائيات:</b>
• إجمالي المستحقات: <b>{total_pending:.2f}</b>
• عدد المحيلين: <b>{len(pending_commissions)}</b>

🛠 <b>اختر الإجراء المناسب:</b>
    """
    
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("💰 توزيع النسب", callback_data="distribute_commissions"),
        types.InlineKeyboardButton("⏸ تأجيل 24 ساعة", callback_data="delay_commissions_1")
    )
    markup.row(
        types.InlineKeyboardButton("🔧 تعديل الإعدادات", callback_data="referral_settings"),
        types.InlineKeyboardButton("❌ إلغاء التوزيع", callback_data="cancel_commissions")
    )
    
    try:
        bot.send_message(ADMIN_CHAT_ID, text, parse_mode="HTML", reply_markup=markup)
    except Exception as e:
        logger.error(f"خطأ في إرسال إشعار التوزيع: {str(e)}")

def distribute_commissions():
    """توزيع العمولات"""
    try:
        pending_commissions = get_pending_commissions()
        distribution_report = "<b>تقرير التوزيع</b>\n\n"
        
        total_distributed = 0
        for commission in pending_commissions:
            referrer_id = commission['referrer_id']
            amount = float(commission['total_pending'])
            
            # إضافة الرصيد للمحيل
            update_wallet_balance(referrer_id, amount)
            total_distributed += amount
            
            # إرسال إشعار للمحيل
            try:
                bot.send_message(
                    referrer_id,
                    f"🎉 <b>مبروك! حصلت على عمولة إحالات</b>\n\n"
                    f"💰 <b>المبلغ: {amount:.2f}</b>\n"
                    f"🌐 <b>تم إضافة المبلغ إلى محفظتك بنجاح</b>",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"خطأ في إرسال إشعار للمحيل: {str(e)}")
            
            # تسجيل في التقرير
            distribution_report += f"المستخدم {referrer_id}: {amount:.2f}\n"
        
        # إعادة تعيين المستحقات
        reset_pending_commissions()
        
        # تحديث موعد التوزيع التالي
        settings = load_referral_settings()
        payout_days = int(settings.get('payout_days', 10))
        next_payout = datetime.now() + timedelta(days=payout_days)
        
        settings['last_payout_date'] = datetime.now().isoformat()
        settings['next_payout_date'] = next_payout.isoformat()
        save_referral_settings(settings)
        
        distribution_report += f"\n<b>المجموع: {total_distributed:.2f}</b>"
        distribution_report += f"\n⏰ <b>موعد التوزيع التالي:</b> {next_payout.strftime('%Y-%m-%d %H:%M')}"
        
        return distribution_report, total_distributed
        
    except Exception as e:
        logger.error(f"خطأ في توزيع العمولات: {str(e)}")
        return None, 0

def silent_reset_commissions():
    """إعادة تعيين المستحقات بصمت"""
    try:
        reset_pending_commissions()
        
        # تحديث موعد التوزيع التالي
        settings = load_referral_settings()
        payout_days = int(settings.get('payout_days', 10))
        next_payout = datetime.now() + timedelta(days=payout_days)
        
        settings['last_payout_date'] = datetime.now().isoformat()
        settings['next_payout_date'] = next_payout.isoformat()
        save_referral_settings(settings)
        
        return True
    except Exception as e:
        logger.error(f"خطأ في إعادة التعيين الصامت: {str(e)}")
        return False

def generate_referral_link(chat_id):
    """إنشاء رابط إحالة"""
    bot_username = bot.get_me().username
    return f"https://t.me/{bot_username}?start=ref_{chat_id}"

def show_referral_section(chat_id, message_id):
    """عرض قسم الإحالات"""
    stats = get_referral_stats(chat_id)
    referral_link = generate_referral_link(chat_id)
    
    text = f"""
<b>👥 نظام الإحالات</b>

📊 <b>إحصائياتك:</b>
• عدد الإحالات: <b>{stats['referral_count']}</b>
• العمولة المعلقة: <b>{stats['pending_commission']:.2f}</b>
• إجمالي العمولة: <b>{stats['total_commission']:.2f}</b>

🔗 <b>رابط الإحالة:</b>
<code>{referral_link}</code>

💡 <b>كيف يعمل النظام:</b>
• تحصل على نسبة {int(float(load_referral_settings().get('commission_rate', 0.1)) * 100)}% من صافي خسارة أحالتك
• يتم التوزيع كل {load_referral_settings().get('payout_days', 10)} أيام
    """
    
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("📋 قائمة الإحالات", callback_data="show_my_referrals"),
        types.InlineKeyboardButton("🔄 تحديث", callback_data="referral_section")
    )
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="main_menu"))
    
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )
    except:
        bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )

def show_my_referrals(chat_id, message_id):
    """عرض إحالات المستخدم"""
    referrals = get_user_referrals(chat_id)
    
    text = "<b>📋 قائمة الإحالات</b>\n\n"
    
    if referrals:
        for i, referral in enumerate(referrals, 1):
            referred_id = referral['referred_id']
            join_date = referral['created_at'].strftime('%Y-%m-%d')
            text += f"{i}. المستخدم {referred_id} - انضم في {join_date}\n"
    else:
        text += "❌ لا توجد إحالات حتى الآن.\n\nاستخدم رابط الإحالة لجلب أعضاء جدد!"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="referral_section"))
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        parse_mode="HTML",
        reply_markup=markup
    )

def show_referral_admin_panel(chat_id, message_id):
    """عرض لوحة إدارة الإحالات"""
    settings = load_referral_settings()
    pending_commissions = get_pending_commissions()
    
    total_pending = sum(commission['total_pending'] for commission in pending_commissions)
    next_payout = datetime.fromisoformat(settings.get('next_payout_date', datetime.now().isoformat()))
    
    text = f"""
<b>👨‍💼 إدارة نظام الإحالات</b>

📊 <b>الإحصائيات:</b>
• النسبة الحالية: <b>{float(settings.get('commission_rate', 0.1)) * 100}%</b>
• أيام التوزيع: <b>{settings.get('payout_days', 10)}</b> يوم
• المستحقات المعلقة: <b>{total_pending:.2f}</b>
• المحيلين النشطين: <b>{len(pending_commissions)}</b>
• موعد التوزيع التالي: <b>{next_payout.strftime('%Y-%m-%d %H:%M')}</b>

🛠 <b>اختر الإجراء المطلوب:</b>
    """
    
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("📈 الإحصائيات", callback_data="referral_stats"),
        types.InlineKeyboardButton("⚙️ الإعدادات", callback_data="referral_settings")
    )
    markup.row(
        types.InlineKeyboardButton("💰 توزيع الآن", callback_data="force_distribute"),
        types.InlineKeyboardButton("🔄 إعادة تعيين", callback_data="silent_reset_confirm")
    )
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel"))
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        parse_mode="HTML",
        reply_markup=markup
    )

def show_referral_settings(chat_id, message_id):
    """عرض إعدادات الإحالات"""
    settings = load_referral_settings()
    
    text = f"""
<b>⚙️ إعدادات الإحالات</b>

📊 <b>الإعدادات الحالية:</b>
• نسبة العمولة: <b>{float(settings.get('commission_rate', 0.1)) * 100}%</b>
• أيام التوزيع: <b>{settings.get('payout_days', 10)}</b> يوم

🛠 <b>اختر الإعداد لتعديله:</b>
    """
    
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("📊 تعديل النسبة", callback_data="edit_commission_rate"),
        types.InlineKeyboardButton("⏰ تعديل الأيام", callback_data="edit_payout_days")
    )
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="referral_admin"))
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        parse_mode="HTML",
        reply_markup=markup
    )

def distribute_commissions_handler(chat_id, message_id):
    """معالج توزيع العمولات"""
    confirm_distribution(chat_id, message_id)
    report, total = distribute_commissions()
    if report:
        bot.send_message(chat_id, report, parse_mode="HTML")
        show_referral_admin_panel(chat_id, message_id)
    else:
        bot.send_message(chat_id, "❌ فشل في توزيع العمولات")

def delay_commissions(days):
    """تأجيل التوزيع"""
    settings = load_referral_settings()
    next_payout = datetime.now() + timedelta(days=days)
    settings['next_payout_date'] = next_payout.isoformat()
    save_referral_settings(settings)

def confirm_silent_reset(chat_id, message_id):
    """تأكيد إعادة التعيين الصامت"""
    text = "⚠️ <b>تأكيد إعادة التعيين الصامت</b>\n\nهل أنت متأكد من إعادة تعيين جميع المستحقات المعلقة؟"
    
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("✅ نعم", callback_data="confirm_silent_reset"),
        types.InlineKeyboardButton("❌ لا", callback_data="referral_admin")
    )
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        parse_mode="HTML",
        reply_markup=markup
    )

def confirm_distribution(chat_id, message_id):
    """تأكيد التوزيع مع رسائل متتالية"""
    # المرحلة الأولى: التأكيد الأساسي
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("نعم، توزيع الآن", callback_data="force_distribute_confirm"),
        types.InlineKeyboardButton("لا، إلغاء", callback_data="referral_admin")
    )
    
    text = (
        "<b>⚠️ تأكيد توزيع العمولات</b>\n\n"
        "<b>هل أنت متأكد من توزيع العمولات الآن؟</b>\n"
        "<b>سيتم:</b>\n"
        "• خصم المبالغ من رصيد النظام\n"
        "• إضافة العمولات لمحافظ المحيلين\n"
        "• إرسال إشعارات للمحيلين\n"
        "• إعادة تعيين المستحقات المعلقة\n\n"
        "<b>هذا الإجراء لا يمكن التراجع عنه</b>"
    )
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        parse_mode="HTML",
        reply_markup=markup
    )

def confirm_distribution_final(chat_id, message_id):
    """التأكيد النهائي قبل التوزيع"""
    pending_commissions = get_pending_commissions()
    total_pending = sum(commission['total_pending'] for commission in pending_commissions)
    
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("✅ نعم، أتأكد وأوافق", callback_data="force_distribute_final"),
        types.InlineKeyboardButton("❌ إلغاء", callback_data="referral_admin")
    )
    
    text = (
        "<b>🚨 التأكيد النهائي</b>\n\n"
        "<b>هل أنت متأكد بنسبة 100%؟</b>\n"
        "<b>سيتم توزيع:</b>\n"
        f"• إجمالي المبلغ: <b>{total_pending:.2f}</b>\n"
        f"• على <b>{len(pending_commissions)}</b> محيل\n"
        "• بشكل فوري ولا رجعة فيه\n\n"
        "<b>سيتم خصم هذا المبلغ من رصيد النظام وإضافته للمحيلين</b>\n\n"
        "<b>اضغط 'نعم' فقط إذا كنت متأكدًا تمامًا</b>"
    )
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        parse_mode="HTML",
        reply_markup=markup
    )

# =============================================================================
# نظام التعويض الخاص
# =============================================================================

def load_compensation_settings():
    """تحميل إعدادات نظام التعويض"""
    result = db_manager.execute_query('SELECT * FROM compensation_settings')
    settings = {}
    if result:
        for row in result:
            settings[row['setting_key']] = row['setting_value']
    
    # القيم الافتراضية
    defaults = {
        'compensation_rate': '0.1',
        'min_loss_amount': '10000',
        'compensation_enabled': 'true'
    }
    
    for key, value in defaults.items():
        if key not in settings:
            settings[key] = value
    
    return settings

def save_compensation_settings(settings):
    """حفظ إعدادات نظام التعويض"""
    for key, value in settings.items():
        success = db_manager.execute_query(
            "INSERT INTO compensation_settings (setting_key, setting_value) VALUES (%s, %s) "
            "ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value, "
            "updated_at = CURRENT_TIMESTAMP",
            (key, str(value))
        )
        if not success:
            return False
    return True

def get_user_net_loss_24h(user_id):
    """حساب صافي خسارة المستخدم خلال 24 ساعة (باستثناء التعويضات السابقة)"""
    try:
        # حساب إجمالي الإيداعات
        deposit_result = db_manager.execute_query(
            "SELECT COALESCE(SUM(amount), 0) as total_deposits FROM transactions "
            "WHERE user_id = %s AND type = 'deposit' AND created_at >= NOW() - INTERVAL '24 hours'",
            (str(user_id),)
        )
        total_deposits = float(deposit_result[0]['total_deposits']) if deposit_result else 0
        
        # حساب إجمالي السحوبات
        withdraw_result = db_manager.execute_query(
            "SELECT COALESCE(SUM(amount), 0) as total_withdrawals FROM transactions "
            "WHERE user_id = %s AND type = 'withdraw' AND created_at >= NOW() - INTERVAL '24 hours'",
            (str(user_id),)
        )
        total_withdrawals = float(withdraw_result[0]['total_withdrawals']) if withdraw_result else 0
        
        # ✅ استبعاد الخسارة التي تم تعويضها مسبقاً
        compensation_tracking = db_manager.execute_query(
            "SELECT last_compensation_loss, last_compensation_date FROM compensation_tracking "
            "WHERE user_id = %s AND last_compensation_date >= NOW() - INTERVAL '24 hours'",
            (str(user_id),)
        )
        
        compensated_loss = 0
        if compensation_tracking and len(compensation_tracking) > 0:
            compensated_loss = float(compensation_tracking[0]['last_compensation_loss'])
            logger.info(f"✅ استبعاد خسارة سابقة تم تعويضها: {compensated_loss} للمستخدم {user_id}")
        
        # صافي الخسارة = الإيداعات - السحوبات - الخسارة المعوضة سابقاً
        net_loss = total_deposits - total_withdrawals - compensated_loss
        return max(0, net_loss)  # إرجاع القيمة الموجبة فقط
        
    except Exception as e:
        logger.error(f"❌ خطأ في حساب صافي الخسارة: {str(e)}")
        return 0

def add_compensation_request(user_id, amount, net_loss, message_id=None, group_chat_id=None):
    """إضافة طلب تعويض"""
    request_id = str(int(time.time() * 1000))
    
    success = db_manager.execute_query(
        "INSERT INTO compensation_requests (request_id, user_id, amount, net_loss, "
        "group_message_id, group_chat_id) VALUES (%s, %s, %s, %s, %s, %s)",
        (request_id, str(user_id), amount, net_loss, message_id, group_chat_id)
    )
    
    return request_id if success else None

def get_compensation_request_by_message(group_chat_id, message_id):
    """جلب طلب التعويض باستخدام معرف الرسالة"""
    result = db_manager.execute_query(
        "SELECT * FROM compensation_requests WHERE group_chat_id = %s AND "
        "group_message_id = %s AND status = 'pending'",
        (str(group_chat_id), message_id)
    )
    
    if result and len(result) > 0:
        row = result[0]
        return {
            'request_id': row['request_id'],
            'user_id': row['user_id'],
            'amount': float(row['amount']),
            'net_loss': float(row['net_loss']),
            'status': row['status'],
            'timestamp': row['created_at'].timestamp() if row['created_at'] else time.time(),
            'group_message_id': row['group_message_id'],
            'group_chat_id': row['group_chat_id']
        }
    
    return None

def is_compensation_request_processed(request_id):
    """التحقق إذا كان طلب التعويض تم معالجته مسبقاً"""
    result = db_manager.execute_query(
        "SELECT status FROM compensation_requests WHERE request_id = %s",
        (request_id,)
    )
    
    if result and len(result) > 0:
        status = result[0]['status']
        return status != 'pending'
    return False

def get_last_3_codes_usage():
    """جلب استخدامات آخر 3 أكواد"""
    try:
        result = db_manager.execute_query("""
            SELECT gc.code, gc.amount, gc.created_at, 
                   gcu.user_id, gcu.used_at, gcu.usage_id
            FROM gift_codes gc
            JOIN gift_code_usage gcu ON gc.code = gcu.code
            WHERE gc.code IN (
                SELECT code FROM gift_codes 
                ORDER BY created_at DESC 
                LIMIT 3
            )
            ORDER BY gc.created_at DESC, gcu.used_at DESC
        """)
        return result if result else []
    except Exception as e:
        logger.error(f"خطأ في جلب استخدامات الأكواد: {str(e)}")
        return []

def revoke_gift_code_usage(usage_id):
    """استرداد مكافأة كود الهدية"""
    try:
        # جلب بيانات الاستخدام
        usage_data = db_manager.execute_query(
            """SELECT gcu.user_id, gcu.amount_received, gcu.code 
               FROM gift_code_usage gcu 
               WHERE gcu.usage_id = %s""",
            (usage_id,)
        )
        
        if not usage_data or len(usage_data) == 0:
            return False, "لم يتم العثور على الاستخدام"
        
        usage_info = usage_data[0]
        user_id = usage_info['user_id']
        amount = usage_info['amount_received']
        code = usage_info['code']
        
        # خصم المبلغ من المستخدم
        current_balance = get_wallet_balance(user_id)
        if current_balance < amount:
            return False, "رصيد المستخدم غير كافي للاسترداد"
        
        new_balance = update_wallet_balance(user_id, -amount)
        
        # تحديث عدد استخدامات الكود
        db_manager.execute_query(
            "UPDATE gift_codes SET used_count = used_count - 1 WHERE code = %s",
            (code,)
        )
        
        # حذف سجل الاستخدام
        db_manager.execute_query(
            "DELETE FROM gift_code_usage WHERE usage_id = %s",
            (usage_id,)
        )
        
        # تسجيل معاملة الاسترداد
        add_transaction({
            'user_id': user_id,
            'type': 'gift_code_revoke',
            'amount': -amount,
            'description': f"استرداد كود: {code}"
        })
        
        return True, f"تم استرداد {amount} من المستخدم {user_id}"
        
    except Exception as e:
        logger.error(f"خطأ في استرداد الكود: {str(e)}")
        return False, "حدث خطأ أثناء الاسترداد"
def show_gift_code_management(chat_id, message_id):
    """عرض إدارة أكواد الهدايا"""
    usages = get_last_3_codes_usage()
    
    if not usages:
        text = "📊 لا توجد استخدامات لأخر 3 أكواد"
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel"))
        
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )
        return
    
    # تجميع البيانات حسب الكود
    codes_data = {}
    for usage in usages:
        code = usage['code']
        if code not in codes_data:
            codes_data[code] = {
                'amount': usage['amount'],
                'created_at': usage['created_at'],
                'usages': []
            }
        codes_data[code]['usages'].append(usage)
    
    text = "📊 <b>استخدامات آخر 3 أكواد</b>\n\n"
    
    for i, (code, data) in enumerate(codes_data.items(), 1):
        text += f"<b>الكود {i}:</b> <code>{code}</code>\n"
        text += f"<b>المبلغ:</b> {data['amount']}\n"
        text += f"<b>وقت الإنشاء:</b> {data['created_at'].strftime('%Y-%m-%d %H:%M')}\n"
        text += f"<b>عدد المستخدمين:</b> {len(data['usages'])}\n\n"
        
        for usage in data['usages']:
            text += f"👤 المستخدم: <code>{usage['user_id']}</code>\n"
            text += f"⏰ الوقت: {usage['used_at'].strftime('%Y-%m-%d %H:%M')}\n"
            text += f"🔄 استرداد: /revoke_{usage['usage_id']}\n"
            text += "────────────────\n"
        
        text += "\n"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔄 تحديث", callback_data="gift_code_manage"))
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel"))
    
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )
    except:
        bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )

def handle_revoke_gift_code(call, usage_id):
    """معالجة استرداد كود الهدية"""
    try:
        success, message = revoke_gift_code_usage(usage_id)
        
        if success:
            bot.answer_callback_query(call.id, f"✅ {message}", show_alert=True)
        else:
            bot.answer_callback_query(call.id, f"❌ {message}", show_alert=True)
            
        # تحديث العرض
        show_gift_code_management(call.message.chat.id, call.message.message_id)
        
    except Exception as e:
        logger.error(f"خطأ في معالجة الاسترداد: {str(e)}")
        bot.answer_callback_query(call.id, "❌ حدث خطأ في الاسترداد", show_alert=True)

@bot.message_handler(func=lambda message: message.text.startswith('/revoke_'))
def handle_revoke_command(message):
    """معالجة أمر الاسترداد السريع"""
    chat_id = str(message.chat.id)
    
    if not is_admin(chat_id):
        bot.send_message(chat_id, "❌ ليس لديك صلاحية لهذا الأمر")
        return
    
    try:
        usage_id = message.text.replace('/revoke_', '').strip()
        
        if not usage_id.isdigit():
            bot.send_message(chat_id, "❌ رقم الاستخدام غير صحيح")
            return
        
        success, msg = revoke_gift_code_usage(int(usage_id))
        
        if success:
            bot.send_message(chat_id, f"✅ {msg}")
        else:
            bot.send_message(chat_id, f"❌ {msg}")
            
    except Exception as e:
        logger.error(f"خطأ في أمر الاسترداد: {str(e)}")
        bot.send_message(chat_id, "❌ حدث خطأ في الاسترداد")
# ===============================================================
# نظام طرق الدفع والسحب
# ===============================================================

class PaymentSystem:
    def __init__(self):
        self.methods = load_payment_methods()

    def add_payment_method(self, name, address, min_amount, exchange_rate=1.0):
        method_id = str(len(self.methods) + 1)
        if len(self.methods) >= 10:
            return None, "❌ وصلت للحد الأقصى (10 طرق)"

        self.methods[method_id] = {
            'name': name,
            'address': address,
            'min_amount': min_amount,
            'exchange_rate': exchange_rate,
            'active': True
        }
        
        save_payment_methods(self.methods)
        return method_id, "✅ تم إضافة طريقة الدفع بنجاح"

    def delete_payment_method(self, method_id):
        if method_id in self.methods:
            del self.methods[method_id]
            save_payment_methods(self.methods)
            return True, "✅ تم حذف طريقة الدفع بنجاح"
        return False, "❌ طريقة الدفع غير موجودة"

    def update_payment_method(self, method_id, **kwargs):
        if method_id in self.methods:
            self.methods[method_id].update(kwargs)
            save_payment_methods(self.methods)
            return True, "✅ تم تحديث طريقة الدفع بنجاح"
        return False, "❌ طريقة الدفع غير موجودة"

    def get_active_methods(self):
        return {k: v for k, v in self.methods.items() if v.get('active', True)}

    def get_method_buttons(self, action_type="payment"):
        methods = self.get_active_methods()
        markup = types.InlineKeyboardMarkup()
        
        for method_id, method in methods.items():
            button_text = f"💳 {method['name']}"
            callback_data = f"{action_type}_method_{method_id}"
            markup.add(types.InlineKeyboardButton(button_text, callback_data=callback_data))
        
        markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="main_menu"))
        return markup

class WithdrawSystem:
    def __init__(self):
        self.methods = load_withdraw_methods()

    def add_withdraw_method(self, name, commission_rate):
        method_id = str(len(self.methods) + 1)
        if len(self.methods) >= 10:
            return None, "❌ وصلت الحد الأقصى (10 طرق)"

        self.methods[method_id] = {
            'name': name,
            'commission_rate': commission_rate,
            'active': True
        }
        
        save_withdraw_methods(self.methods)
        return method_id, "✅ تم إضافة طريقة السحب بنجاح"

    def delete_withdraw_method(self, method_id):
        if method_id in self.methods:
            del self.methods[method_id]
            save_withdraw_methods(self.methods)
            return True, "✅ تم حذف طريقة السحب بنجاح"
        return False, "❌ طريقة السحب غير موجودة"

    def update_withdraw_method(self, method_id, **kwargs):
        if method_id in self.methods:
            self.methods[method_id].update(kwargs)
            save_withdraw_methods(self.methods)
            return True, "✅ تم تحديث طريقة السحب بنجاح"
        return False, "❌ طريقة السحب غير موجودة"

    def get_active_methods(self):
        return {k: v for k, v in self.methods.items() if v.get('active', True)}

    def get_method_buttons(self):
        methods = self.get_active_methods()
        markup = types.InlineKeyboardMarkup()
        
        for method_id, method in methods.items():
            button_text = f"💸 {method['name']}"
            markup.add(types.InlineKeyboardButton(button_text, 
                        callback_data=f"withdraw_method_{method_id}"))
        
        markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="main_menu"))
        return markup

# إنشاء أنظمة الدفع والسحب
payment_system = PaymentSystem()
withdraw_system = WithdrawSystem()

# ===============================================================
# نظام الواجهة المحسنة - التصميم الجديد
# ===============================================================

class EnhancedKeyboard:
    @staticmethod
    def create_main_menu(has_account=False, is_admin=False):
        markup = types.InlineKeyboardMarkup()
        
        # الزر الرئيسي في الأعلى
        markup.add(types.InlineKeyboardButton("⚡ حساب 55BETS وشحنه ⚡", callback_data="account_section"))
        
        
        markup.add(types.InlineKeyboardButton("⛓️ رابط موقع 55BETS", url="https://www.55bets.net/"))
        # أزرار طرق السحب والدفع
        markup.row(
            types.InlineKeyboardButton("  📤 سحب حوالة مالية  ", callback_data="withdraw_methods"),
            types.InlineKeyboardButton("  📥 شحن محفظة البوت  ", callback_data="payment_methods")
        )
        
        # زر سجل الرصيد والإحالات
        markup.add(types.InlineKeyboardButton("💳 سجل الرصيد", callback_data="balance_history"))
        
        markup.add(types.InlineKeyboardButton("🛡️ التعويض الخاص", callback_data="compensation_section"),
            types.InlineKeyboardButton("🎖 نقاط الامتياز", callback_data="loyalty_section"))
        
        markup.add(types.InlineKeyboardButton("🎁 إهداء الرصيد", callback_data="gift_balance"),
            types.InlineKeyboardButton("🎟 كود هدية", callback_data="gift_code"))
        markup.add(types.InlineKeyboardButton("📞 التواصل مع الدعم", callback_data="contact_support"))
        
        markup.add(types.InlineKeyboardButton("👥 نظام الإحالات", callback_data="referral_section"))
        
        
        
        
        markup.add(types.InlineKeyboardButton("🔄 استرداد آخر طلب سحب", callback_data="refund_last_withdrawal"),
            types.InlineKeyboardButton("📋 سجل السحوبات", callback_data="withdraw_history"))
        markup.add(types.InlineKeyboardButton("📜 الشروط والأحكام", callback_data="show_terms"))
        
        
        
        
        if is_admin:
            markup.add(types.InlineKeyboardButton("⚙️ لوحة الإدارة", callback_data="admin_panel"))
        
        return markup

    @staticmethod
    def create_account_section(has_account=False):
        markup = types.InlineKeyboardMarkup()
        
        if not has_account:
            markup.add(types.InlineKeyboardButton("🆕 إنشاء حساب جديد", callback_data="create_account"))
        else:
            markup.add(types.InlineKeyboardButton("👤 معلومات حسابي", callback_data="show_account"))
            
            markup.add(types.InlineKeyboardButton("⛓️ رابط موقع 55BETS", url="https://www.55bets.net/"))
            
            markup.row(
                types.InlineKeyboardButton("↙️ شحن الحساب", callback_data="deposit_to_account"),
                types.InlineKeyboardButton("↗️ السحب من الحساب", callback_data="withdraw_from_account")
            )
        
        markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="main_menu"))
        return markup

    @staticmethod
    def create_back_button(target="main_menu"):
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data=target))
        return markup

    @staticmethod
    def create_confirmation_buttons(confirm_data, cancel_data="main_menu"):
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton("✅ تأكيد", callback_data=confirm_data),
            types.InlineKeyboardButton("❌ إلغاء", callback_data=cancel_data)
        )
        return markup

    @staticmethod
    def create_admin_panel():
        markup = types.InlineKeyboardMarkup()
        
        markup.row(
            types.InlineKeyboardButton("💳 إدارة الدفع", callback_data="manage_payment_methods"),
            types.InlineKeyboardButton("💸 إدارة السحب", callback_data="manage_withdraw_methods")
        )
        
        markup.row(
            types.InlineKeyboardButton("👥 إدارة الإحالات", callback_data="referral_admin"),
            types.InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats")
        )
        
        markup.row(
            types.InlineKeyboardButton("👤 إدارة المستخدمين", callback_data="manage_users"),
            types.InlineKeyboardButton("🔧 الصيانة", callback_data="maintenance_settings")
        )
        markup.row(
            types.InlineKeyboardButton("🎖 إدارة النقاط", callback_data="loyalty_admin"),
            types.InlineKeyboardButton("🛡️ إدارة التعويض", callback_data="compensation_admin"))
        
        markup.row(
        types.InlineKeyboardButton("🎁 إدارة الإهداء", callback_data="gift_admin")
    )
        markup.row(
            types.InlineKeyboardButton("إنشاء كود هدية", callback_data="gift_code_admin"),
            types.InlineKeyboardButton("إدارة الأكواد", callback_data="gift_code_manage")
    )
        markup.add(types.
InlineKeyboardButton("🔙 رجوع", callback_data="main_menu"))
        return markup

# ===============================================================
# دوال الوسيط المحسنة
# ===============================================================

def create_account_via_agent(username, password):
    """إنشاء حساب عبر الوكيل"""
    email = f"{username}@gmail.com"
    result = agent.register_player(username, password, email)
    success = result.get("status", False) if result and "error" not in result else False
    return success, result

def get_player_id_via_agent(username):
    """الحصول على معرف اللاعب عبر الوكيل"""
    try:
        start = 0
        limit = 100
        while True:
            players_data = agent.get_players(start, limit)
            if not players_data or "error" in players_data:
                break

            records = []
            if "result" in players_data and "records" in players_data["result"]:
                records = players_data["result"]["records"]
            elif "records" in players_data:
                records = players_data["records"]

            if not records:
                break

            for player in records:
                player_username = player.get("username", "") or player.get("login", "")
                player_id = player.get("playerId") or player.get("id")
                if player_username and player_username.lower() == username.lower():
                    return player_id

            if len(records) < limit:
                break
            start += limit
            time.sleep(0.5)
        return None
    except Exception as e:
        logger.error(f"❌ خطأ في البحث عن اللاعب: {e}")
        return None

def get_player_balance_via_agent(player_id):
    """الحصول على رصيد اللاعب عبر الوكيل"""
    try:
        result = agent.get_player_balance(player_id)
        if result and "error" not in result:
            if isinstance(result, dict):
                balance_data = result.get("result", [{}])[0] if isinstance(result.get("result"), list) else result.get("result", {})
                return float(balance_data.get("balance", 0))
        return 0
    except Exception as e:
        logger.error(f"❌ خطأ في جلب رصيد اللاعب: {e}")
        return 0

def deposit_to_account_via_agent(player_id, amount):
    """شحن الحساب عبر الوكيل"""
    return agent.deposit_to_player(player_id, amount)

def withdraw_from_account_via_agent(player_id, amount):
    """سحب من الحساب عبر الوكيل"""
    return agent.withdraw_from_player(player_id, amount)

# ===============================================================
# نظام معالجة المهام - طابور موحد
# ===============================================================

def process_account_operations():
    """معالجة المهام من طابور عمليات الحساب"""
    while True:
        if not account_operations_queue.empty():
            task = account_operations_queue.get()
            task_type = task.get('type')
            
            try:
                if task_type == 'create_account':
                    process_account_creation(task)
                elif task_type == 'deposit_to_account':
                    process_deposit_to_account(task)
                elif task_type == 'withdraw_from_account':
                    process_withdraw_from_account(task)
                    
            except Exception as e:
                logger.error(f"❌ خطأ في معالجة المهمة: {e}")
                chat_id = task.get('chat_id')
                if chat_id:
                    try:
                        bot.send_message(chat_id, "❌ حدث خطأ أثناء المعالجة. يرجى المحاولة لاحقاً.")
                    except:
                        pass
            finally:
                account_operations_queue.task_done()
        else:
            time.sleep(1)

def process_account_creation(task):
    """معالجة إنشاء الحساب"""
    chat_id = task['chat_id']
    username = task['username']
    password = task['password']
    
    try:
        # إضافة اللاحقة العشوائية
        final_username = f"{username}_{generate_suffix()}"
        
        # إنشاء الحساب عبر الوسيط
        success, result = create_account_via_agent(final_username, password)
        
        if success:
            # البحث عن معرف اللاعب
            player_id = get_player_id_via_agent(final_username)
            
            # حفظ بيانات الحساب
            account_data = {
                "username": final_username,
                "password": password,
                "playerId": player_id,
                "created_at": time.time()
            }
            
            accounts = load_accounts()
            accounts[str(chat_id)] = account_data
            save_accounts(accounts)
            
            # إرسال رسالة النجاح
            success_text = f"""
<b>✅ تم إنشاء الحساب بنجاح</b>

👤 <b>اسم المستخدم:</b> <code>{final_username}</code>
🔐 <b>كلمة المرور:</b> <code>{password}</code>
🆔 <b>رقم اللاعب:</b> <code>{player_id if player_id else 'جاري التحميل...'}</code>

💡 <i>احتفظ ببيانات حسابك في مكان آمن</i>
            """
            
            bot.send_message(chat_id, success_text, parse_mode="HTML")
        else:
            bot.send_message(chat_id, "❌ فشل في إنشاء الحساب. يرجى المحاولة لاحقاً.")
            
    except Exception as e:
        error_msg = f"❌ حدث خطأ أثناء إنشاء الحساب: {str(e)}"
        bot.send_message(chat_id, error_msg)

def process_deposit_to_account(task):
    """معالجة شحن الحساب"""
    chat_id = task['chat_id']
    amount = task['amount']
    player_id = task['player_id']

    try:
        # التحقق من رصيد الكاشير أولاً
        if not check_cashier_balance_sufficient(amount):
            # إشعار المستخدم
            bot.send_message(
                chat_id,
                f"""<b>❌ عملية شحن فشلت</b>

رصيد الكاشير غير كافي حالياً.
سيتم إعلام الإدارة بالمحاولة.""",
                parse_mode="HTML"
            )

            # إشعار الإدارة
            try:
                bot.send_message(
                    ADMIN_CHAT_ID,
                    f"""<b>❌ عملية شحن فشلت</b>

المستخدم: <code>{chat_id}</code>
المبلغ: {amount}
السبب: رصيد الكاشير غير كافي
الوقت: {time.strftime("%Y-%m-%d %H:%M:%S")}""",
                    parse_mode="HTML"
                )
            except:
                pass
            return

        # التحقق من رصيد المحفظة
        wallet_balance = get_wallet_balance(chat_id)
        if wallet_balance < amount:
            bot.send_message(chat_id, f"❌ رصيدك غير كافي. رصيدك الحالي: {wallet_balance}")
            return

        # محاولة الشحن عبر الوسيط
        success = deposit_to_account_via_agent(player_id, amount)  # ✅ تعريف success هنا

        if success:
            # خصم المبلغ من المحفظة
            new_balance = update_wallet_balance(chat_id, -amount)

            # ✅ تسجيل معاملة الشحن في قاعدة البيانات
            transaction_data = {
                'user_id': str(chat_id),
                'type': 'deposit',
                'amount': amount,
                'description': f'شحن حساب 55BETS - Player ID: {player_id}'
            }
            transaction_id = add_transaction(transaction_data)  # ✅ الحصول على transaction_id

            # ✅ إشعار الإدارة بعملية الشحن الناجحة
            try:
                bot.send_message(
                    ADMIN_CHAT_ID,
                    f"""<b>✅ عملية شحن ناجحة</b>

المستخدم: <code>{chat_id}</code>
المبلغ: <b>{amount}</b>
معرف اللاعب: <code>{player_id}</code>
رصيد المحفظة الجديد: <b>{new_balance}</b>
الوقت: {time.strftime("%Y-%m-%d %H:%M:%S")}""",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"خطأ في إرسال إشعار الشحن للإدارة: {str(e)}")

            # تسجيل عملية الشحن للإحالات
            referrer_id = get_referrer(chat_id)
            if referrer_id:
                settings = load_referral_settings()
                commission_rate = float(settings.get('commission_rate', 0.1))
                commission_amount = amount * commission_rate

                # إضافة للعمولات المعلقة
                update_referral_earning(referrer_id, commission_amount)

                # تسجيل في السجل
                log_referral_commission(referrer_id, chat_id, 'deposit', amount, 0,
                                      commission_amount)

                logger.info(f"إضافة عمولة إحالة عند الشحن: {commission_amount}")

            # إضافة نقاط الولاء للشحن
            settings = load_loyalty_settings()
            points_per_10000 = int(settings.get('points_per_10000', 1))
            points_earned = (amount // 10000) * points_per_10000

            if points_earned > 0:
                add_loyalty_points(chat_id, points_earned, f"شحن مبلغ {amount}")

            # ✅ إضافة نقاط المكافأة الأولى للمحيل - التصحيح هنا
            if referrer_id:
                # التحقق إذا كانت هذه أول عملية شحن للمحال
                # الآن نتحقق من عدد عمليات الشحن باستثناء العملية الحالية
                first_deposit_result = db_manager.execute_query(
                    "SELECT COUNT(*) as deposit_count FROM transactions "
                    "WHERE user_id = %s AND type = 'deposit' AND transaction_id != %s",
                    (str(chat_id), transaction_id if transaction_id else '0')
                )

                deposit_count = first_deposit_result[0]['deposit_count'] if first_deposit_result else 0

                # ✅ إذا لم يكن هناك أي عمليات شحن سابقة (count = 0)
                if deposit_count == 0:
                    first_deposit_bonus = int(settings.get('first_deposit_bonus', 3))
                    add_loyalty_points(referrer_id, first_deposit_bonus, "مكافأة أول إيداع للمحيل")
                    logger.info(f"تم إضافة {first_deposit_bonus} نقطة مكافأة للمحيل {referrer_id} لأول إيداع")
                else:
                    logger.info(f"المستخدم {chat_id} لديه {deposit_count} عملية شحن سابقة - لا مكافأة أول إيداع")

            # إرسال رسالة نجاح للمستخدم
            bot.send_message(
                chat_id,
                f"""<b>✅ شحن الحساب بنجاح</b>

المبلغ المشحون: <b>{amount}</b>
رصيد محفظتك الحالي: <b>{new_balance}</b>""",
                parse_mode="HTML"
            )

        else:
            # إرسال رسالة فشل للمستخدم
            bot.send_message(
                chat_id,
                """<b>❌ فشل في شحن الحساب</b>

السبب: رصيد الكاشير غير كافي أو حدث خطأ في النظام
الحل: تأكد من رصيد الكاشير وحاول مرة أخرى""",
                parse_mode="HTML"
            )

            # ✅ إشعار الإدارة بفشل عملية الشحن
            try:
                bot.send_message(
                    ADMIN_CHAT_ID,
                    f"""<b>❌ فشل في شحن الحساب</b>

المستخدم: <code>{chat_id}</code>
المبلغ: {amount}
معرف اللاعب: <code>{player_id}</code>
السبب: فشل في API الوسيط
الوقت: {time.strftime("%Y-%m-%d %H:%M:%S")}""",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"خطأ في إرسال إشعار فشل الشحن للإدارة: {str(e)}")

    except Exception as e:
        error_msg = f"حدث خطأ أثناء الشحن: {str(e)}"
        logger.error(error_msg)
        bot.send_message(chat_id, error_msg)
        
        # ✅ إشعار الإدارة بالخطأ
        try:
            bot.send_message(
                ADMIN_CHAT_ID,
                f"""<b>❌ خطأ في عملية الشحن</b>

المستخدم: <code>{chat_id}</code>
المبلغ: {amount}
معرف اللاعب: <code>{player_id}</code>
الخطأ: {str(e)}
الوقت: {time.strftime("%Y-%m-%d %H:%M:%S")}""",
                parse_mode="HTML"
            )
        except Exception as admin_error:
            logger.error(f"خطأ في إرسال إشعار الخطأ للإدارة: {str(admin_error)}")

def process_withdraw_from_account(task):
    """معالجة سحب من الحساب"""
    chat_id = task['chat_id']
    amount = task['amount']
    player_id = task['player_id']
    
    try:
        # محاولة السحب مباشرة عبر الوسيط
        success = withdraw_from_account_via_agent(player_id, amount)
        
        if success:
            # إضافة المبلغ إلى المحفظة
            new_balance = update_wallet_balance(chat_id, amount)
            
            # تسجيل معاملة السحب
            transaction_data = {
                'user_id': str(chat_id),
                'type': 'withdraw', 
                'amount': amount,
                'description': f'سحب من حساب 55BETS - Player ID: {player_id}'
            }
            add_transaction(transaction_data)
            
            # ✅ معالجة عمولة الإحالات عند السحب (منطق صحيح)
            referrer_id = get_referrer(chat_id)
            if referrer_id:
                try:
                    settings = load_referral_settings()
                    commission_rate = float(settings.get('commission_rate', 0.1))
                    commission_to_deduct = amount * commission_rate
                    
                    # خصم العمولة من العمولات المعلقة
                    success_deduction = deduct_referral_earning(referrer_id, commission_to_deduct)
                    
                    if success_deduction:
                        # تسجيل في سجل العمولات
                        log_referral_commission(referrer_id, chat_id, 'withdraw', amount, 0, commission_to_deduct)
                        logger.info(f"تم خصم عمولة إحالة عند السحب: {commission_to_deduct}")
                    else:
                        logger.error(f"فشل في خصم عمولة الإحالة للمحيل: {referrer_id}")
                        
                except Exception as referral_error:
                    logger.error(f"خطأ في معالجة عمولة السحب: {str(referral_error)}")
            
            # إرسال رسالة نجاح للمستخدم
            bot.send_message(chat_id, 
                f"<b>تم السحب من الحساب بنجاح ✅</b>\n\n"
                f"<b>المبلغ المسحوب:</b> {amount}\n"
                f"<b>رصيد محفظتك الحالي:</b> {new_balance}",
                parse_mode="HTML"
            )
            
    except Exception as e:
        error_msg = f"حدث خطأ أثناء السحب: {str(e)}"
        logger.error(error_msg)
        bot.send_message(chat_id, error_msg)



def deduct_referral_earning(referrer_id, amount):
    """خصم من العمولات المعلقة"""
    try:
        amount_float = float(amount)
        
        # ✅ السماح بالعمولة السالبة (دين على المحيل)
        return db_manager.execute_query(
            "UPDATE referral_earnings SET pending_commission = pending_commission - %s WHERE referrer_id = %s",
            (amount_float, str(referrer_id))
        )
        
    except Exception as e:
        logger.error(f"خطأ في deduct_referral_earning: {str(e)}")
        return False

# ===============================================================
# معالجات البوت الرئيسية
# ===============================================================

@bot.message_handler(commands=['start'])
def start(message):
    chat_id = str(message.chat.id)
    

    # التحقق من وضع الصيانة
    if is_maintenance_mode() and not is_admin(chat_id):
        maintenance = load_maintenance()
        bot.send_message(
            chat_id, 
            f"<b>🔧 الصيانة</b>\n\n{maintenance.get('message', 'البوت في حالة صيانة مؤقتة، يرجى التحلي بالصبر.')}",
            parse_mode="HTML"
        )
        return
    
    # التحقق من الحظر
    if is_user_banned(chat_id):
        bot.send_message(chat_id, "❌ تم حظرك من استخدام البوت.")
        return
    
    # التحقق من الاشتراك في القناة
    if not is_user_subscribed(message.from_user.id):
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("📢 الاشتراك في القناة", url=CHANNEL_LINK))
        markup.add(types.InlineKeyboardButton("✅ تم الاشتراك", callback_data="check_subscription"))
        
        bot.send_message(
            chat_id,
            "<b>عذراً، يجب عليك الاشتراك في قناتنا أولاً</b>",
            parse_mode="HTML",
            reply_markup=markup
        )
        return
    if len(message.text.split()) > 1:
        referral_code = message.text.split()[1]
        if referral_code.startswith('ref_'):
            referrer_id = referral_code.replace('ref_', '')
            if referrer_id != chat_id:  # منع الإحالة الذاتية
               if add_referral(referrer_id, chat_id):
                    try:
                        bot.send_message(
                            referrer_id,
                            f"🎉 <b>إحالة جديدة!</b>\n\nتم انضمام مستخدم جديد عبر رابط إحالتك: <code>{chat_id}</code>  وحصلت على نقطة امتياز جديدة ⚔️",
                            parse_mode="HTML"
                        )
                        referrer_notified = True
                    except Exception as e:
                        logger.error(f"❌ خطأ في إرسال إشعار الإحالة: {e}")
    
    
    accounts = load_accounts()
    has_account = str(chat_id) in accounts
    
    welcome_text = """
<b>مرحباً بك في بوت 55BETS! 🤖</b>

اختر من القائمة:
    """
    
    bot.send_message(
        chat_id,
        welcome_text,
        parse_mode="HTML",
        reply_markup=EnhancedKeyboard.create_main_menu(has_account, is_admin(chat_id))
    )

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    chat_id = str(call.message.chat.id)
    message_id = call.message.message_id
    
    # التحقق من وضع الصيانة
    if is_maintenance_mode() and not is_admin(chat_id):
        maintenance = load_maintenance()
        bot.answer_callback_query(call.id, "البوت في حالة صيانة", show_alert=True)
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"<b>🔧 الصيانة</b>\n\n{maintenance.get('message')}",
            parse_mode="HTML"
        )
        return
    
    try:
        if call.data == "main_menu":
            show_main_menu(chat_id, message_id)
            
        elif call.data == "check_subscription":
            handle_subscription_check(call, chat_id, message_id)
            
        elif call.data == "account_section":
            show_account_section(chat_id, message_id)
            
        elif call.data == "create_account":
            start_account_creation(chat_id)
            
        elif call.data == "show_account":
            show_account_info(chat_id, message_id)
            
        elif call.data == "deposit_to_account":
            start_deposit_to_account(chat_id)
            
        elif call.data == "withdraw_from_account":
            start_withdraw_from_account(chat_id)
            
        elif call.data == "payment_methods":
            show_payment_methods(chat_id, message_id)
            
        elif call.data == "withdraw_methods":
            show_withdraw_methods(chat_id, message_id)
            
        elif call.data == "balance_history":
            show_balance_history(chat_id, message_id)
            
        elif call.data == "admin_panel":
            if is_admin(chat_id):
                show_admin_panel(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, "ليس لديك صلاحية الدخول", show_alert=True)
                
        elif call.data == "referral_section":
            show_referral_section(chat_id, message_id)

        elif call.data == "show_my_referrals":
            show_my_referrals(chat_id, message_id)

        # معالجات إدارة الإحالات
        elif call.data == "referral_admin":
            if is_admin(chat_id):
                show_referral_admin_panel(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "referral_settings":
            if is_admin(chat_id):
                show_referral_settings(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "referral_stats":
            if is_admin(chat_id):
                show_referral_stats(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        # معالجات التوزيع
        elif call.data == "distribute_commissions":
            if is_admin(chat_id):
                distribute_commissions_handler(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "delay_commissions_1":
            if is_admin(chat_id):
                delay_commissions(1)
                bot.answer_callback_query(call.id, text="✅ تم التأجيل 24 ساعة")
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "cancel_commissions":
            if is_admin(chat_id):
                silent_reset_commissions()
                bot.answer_callback_query(call.id, text="✅ تم الإلغاء وإعادة التعيين")
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "silent_reset_confirm":
            if is_admin(chat_id):
                confirm_silent_reset(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "confirm_silent_reset":
            if is_admin(chat_id):
                silent_reset_commissions()
                bot.answer_callback_query(call.id, text="✅ تم إعادة التعيين الصامت")
                show_referral_admin_panel(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)
        elif call.data == "force_distribute":
            if is_admin(chat_id):
                confirm_distribution(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "force_distribute_confirm":
            if is_admin(chat_id):
                confirm_distribution_final(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "force_distribute_final":
            if is_admin(chat_id):
                report, total = distribute_commissions()
                if report:
                    bot.send_message(chat_id, report, parse_mode="HTML")
                    show_referral_admin_panel(chat_id, message_id)
                else:
                    bot.send_message(chat_id, "❌ فشل في توزيع العمولات")
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)
        # معالجات إدارة طرق الدفع
        elif call.data == "manage_payment_methods":
            if is_admin(chat_id):
                show_manage_payment_methods(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "add_payment_method":
            if is_admin(chat_id):
                start_add_payment_method(chat_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data.startswith("edit_payment_method_"):
            if is_admin(chat_id):
                method_id = call.data.replace("edit_payment_method_", "")
                start_edit_payment_method(chat_id, method_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data.startswith("delete_payment_method_"):
            if is_admin(chat_id):
                method_id = call.data.replace("delete_payment_method_", "")
                confirm_delete_payment_method(chat_id, message_id, method_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data.startswith("confirm_delete_payment_"):
            if is_admin(chat_id):
                method_id = call.data.replace("confirm_delete_payment_", "")
                success, message = payment_system.delete_payment_method(method_id)
                bot.answer_callback_query(call.id, text=message)
                show_manage_payment_methods(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        # معالجات إدارة طرق السحب
        elif call.data == "manage_withdraw_methods":
            if is_admin(chat_id):
                show_manage_withdraw_methods(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "add_withdraw_method":
            if is_admin(chat_id):
                start_add_withdraw_method(chat_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data.startswith("edit_withdraw_method_"):
            if is_admin(chat_id):
                method_id = call.data.replace("edit_withdraw_method_", "")
                start_edit_withdraw_method(chat_id, method_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data.startswith("delete_withdraw_method_"):
            if is_admin(chat_id):
                method_id = call.data.replace("delete_withdraw_method_", "")
                confirm_delete_withdraw_method(chat_id, message_id, method_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data.startswith("confirm_delete_withdraw_"):
            if is_admin(chat_id):
                method_id = call.data.replace("confirm_delete_withdraw_", "")
                success, message = withdraw_system.delete_withdraw_method(method_id)
                bot.answer_callback_query(call.id, text=message)
                show_manage_withdraw_methods(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)
                
        elif call.data.startswith("payment_method_"):
            method_id = call.data.replace("payment_method_", "")
            start_payment_process(chat_id, message_id, method_id)
            
        elif call.data.startswith("withdraw_method_"):
            method_id = call.data.replace("withdraw_method_", "")
            start_withdraw_process(chat_id, method_id)
            
        elif call.data == "force_distribute":
            if is_admin(chat_id):
                distribute_commissions_handler(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)
                
        elif call.data == "edit_commission_rate":
            if is_admin(chat_id):
                start_edit_commission_rate(chat_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)
                
        elif call.data == "edit_payout_days":
            if is_admin(chat_id):
                start_edit_payout_days(chat_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)
        
        elif call.data.startswith("approve_payment_"):
            handle_approve_payment(call, chat_id, message_id)
    
        elif call.data.startswith("reject_payment_"):
            handle_reject_payment(call, chat_id, message_id)
    
        elif call.data.startswith("complete_withdraw_"):
            handle_complete_withdrawal(call, chat_id, message_id)
        
        elif call.data == "loyalty_section":
            show_loyalty_section(chat_id, message_id)

        elif call.data == "loyalty_leaderboard":
            show_loyalty_leaderboard(chat_id, message_id)

        elif call.data == "loyalty_redeem":
            show_loyalty_redeem(chat_id, message_id)

        elif call.data == "loyalty_history":
            show_loyalty_history(chat_id, message_id)

        elif call.data.startswith("redeem_"):
            reward_id = call.data.replace("redeem_", "")
            redemption_id, message_text = create_redemption_request(chat_id, reward_id)
            bot.answer_callback_query(call.id, text=message_text)
            if redemption_id:
                show_loyalty_redeem(chat_id, message_id)

        # معالجات الإدارة
        elif call.data == "loyalty_admin":
            if is_admin(chat_id):
                show_loyalty_admin_panel(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "loyalty_settings":
            if is_admin(chat_id):
                show_loyalty_settings_admin(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "loyalty_requests":
            if is_admin(chat_id):
                show_pending_redemption_requests(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "loyalty_toggle":
            if is_admin(chat_id):
                # تفعيل/تعطيل النظام
                settings = load_loyalty_settings()
                current_status = settings.get('redemption_enabled', 'false')
                new_status = 'true' if current_status == 'false' else 'false'
                settings['redemption_enabled'] = new_status
                save_loyalty_settings(settings)
        
                status_text = "مفعل" if new_status == 'true' else "معطل"
                bot.answer_callback_query(call.id, text=f"تم {status_text} نظام الاستبدال")
                show_loyalty_admin_panel(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        # معالجات تعديل الإعدادات
        elif call.data == "edit_points_per_10000":
            if is_admin(chat_id):
                user_data[chat_id] = {'state': 'edit_points_per_10000'}
                bot.send_message(chat_id, "أرسل عدد النقاط لكل 10,000:")
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)
        
        # معالجات تعديل الإعدادات المتبقية
        elif call.data == "edit_referral_points":
            if is_admin(chat_id):
                user_data[chat_id] = {'state': 'edit_referral_points'}
                bot.send_message(chat_id, "أرسل عدد نقاط الإحالة:")
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "edit_deposit_bonus":
            if is_admin(chat_id):
                user_data[chat_id] = {'state': 'edit_deposit_bonus'}
                bot.send_message(chat_id, "أرسل عدد نقاط مكافأة الإيداع الأولى:")
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "edit_min_redemption":
            if is_admin(chat_id):
                user_data[chat_id] = {'state': 'edit_min_redemption'}
                bot.send_message(chat_id, "أرسل الحد الأدنى لنقاط الاستبدال:")
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "edit_reset_days":
            if is_admin(chat_id):
                user_data[chat_id] = {'state': 'edit_reset_days'}
                bot.send_message(chat_id, "أرسل عدد أيام التصفير:")
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        # معالجات إدارة الجوائز
        elif call.data == "manage_rewards":
            if is_admin(chat_id):
                show_rewards_management(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "add_reward":
            if is_admin(chat_id):
                user_data[chat_id] = {'state': 'add_reward_name'}
                bot.send_message(
                    chat_id,
                    "<b>إضافة جائزة جديدة</b>\n\nالخطوة 1/4: أرسل اسم الجائزة\nمثال: <em>10$</em>",
                    parse_mode="HTML",
                    reply_markup=EnhancedKeyboard.create_back_button("manage_rewards")
                )
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data.startswith("toggle_reward_"):
            if is_admin(chat_id):
                reward_id = call.data.replace('toggle_reward_', '')
        
                # جلب الحالة الحالية
                result = db_manager.execute_query(
                    "SELECT active FROM loyalty_rewards WHERE reward_id = %s",
                    (reward_id,)
                )
        
                if result and len(result) > 0:
                    current_status = result[0]['active']
                    new_status = not current_status
            
                    # تحديث الحالة
                    success = db_manager.execute_query(
                        "UPDATE loyalty_rewards SET active = %s WHERE reward_id = %s",
                        (new_status, reward_id)
                    )
            
                    if success:
                        status_text = "مفعل" if new_status else "معطل"
                        bot.answer_callback_query(call.id, text=f"تم {status_text} الجائزة")
                        show_rewards_management(chat_id, message_id)
                    else:
                        bot.answer_callback_query(call.id, text="فشل في تحديث الجائزة", show_alert=True)
                else:
                    bot.answer_callback_query(call.id, text="الجائزة غير موجودة", show_alert=True)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data.startswith("edit_reward_"):
            if is_admin(chat_id):
                reward_id = call.data.replace('edit_reward_', '')
                # يمكنك إضافة منطق تعديل الجائزة هنا
                bot.answer_callback_query(call.id, text="خاصية تعديل الجائزة قيد التطوير", show_alert=True)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        # معالجات إضافية للإدارة
        elif call.data == "loyalty_stats":
            if is_admin(chat_id):
                handle_loyalty_stats(call)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "reset_all_points":
            if is_admin(chat_id):
                markup = types.InlineKeyboardMarkup()
                markup.row(
                    types.InlineKeyboardButton("✅ نعم، تصفير الكل", callback_data="confirm_reset_all_points"),
                    types.InlineKeyboardButton("❌ إلغاء", callback_data="loyalty_admin")
                )
        
                bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="<b>⚠️ تأكيد تصفير جميع النقاط</b>\n\nهل أنت متأكد من تصفير جميع نقاط المستخدمين؟ هذا الإجراء لا يمكن التراجع عنه.",
                    parse_mode="HTML",
                    reply_markup=markup
        )
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "confirm_reset_all_points":
            if is_admin(chat_id):
                try:
                    # تصفير جميع النقاط
                    success = db_manager.execute_query("UPDATE loyalty_points SET points = 0, last_reset = CURRENT_TIMESTAMP")
            
                    if success:
                        # تسجيل في السجل
                        db_manager.execute_query("""
                            INSERT INTO loyalty_points_history (user_id, points_change, reason)
                            SELECT user_id, -points, 'تصفير جماعي من الإدارة'
                            FROM loyalty_points
                            WHERE points > 0
                        """)
                
                        bot.answer_callback_query(call.id, text="تم تصفير جميع النقاط بنجاح")
                        show_loyalty_admin_panel(chat_id, message_id)
                    else:
                        bot.answer_callback_query(call.id, text="فشل في تصفير النقاط", show_alert=True)
        
                except Exception as e:
                    logger.error(f"خطأ في تصفير النقاط: {str(e)}")
                    bot.answer_callback_query(call.id, text="حدث خطأ في التصفير", show_alert=True)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "export_points_data":
            if is_admin(chat_id):
                try:
                    # جلب بيانات النقاط
                    result = db_manager.execute_query("""
                        SELECT user_id, points, last_reset, updated_at
                        FROM loyalty_points 
                        WHERE points > 0 
                        ORDER BY points DESC
                    """)
            
                    if result:
                        # إنشاء ملف نصي
                        csv_data = "User ID,Points,Last Reset,Last Update\n"
                        for row in result:
                            csv_data += f"{row['user_id']},{row['points']},{row['last_reset']},{row['updated_at']}\n"
                
                        # إرسال كملف
                        bot.send_document(
                            chat_id,
                            ('loyalty_points.csv', csv_data.encode('utf-8')),
                            caption="<b>📊 تصدير بيانات نقاط الامتياز</b>",
                            parse_mode="HTML"
                        )
                        bot.answer_callback_query(call.id, text="تم تصدير البيانات")
                    else:
                        bot.answer_callback_query(call.id, text="لا توجد بيانات للتصدير", show_alert=True)
        
                except Exception as e:
                    logger.error(f"خطأ في تصدير البيانات: {str(e)}")
                    bot.answer_callback_query(call.id, text="حدث خطأ في التصدير", show_alert=True)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)

        # معالجات طلبات الاستبدال
        elif call.data.startswith("approve_redemption_"):
            if is_admin(chat_id):
                handle_approve_redemption(call)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الموافقة", show_alert=True)

        elif call.data.startswith("reject_redemption_"):
            if is_admin(chat_id):
                handle_reject_redemption(call)
            else:
                bot.answer_callback_query(call.id, text="ليس لديك صلاحية الرفض", show_alert=True)
        
        elif call.data == "compensation_section":
            show_compensation_section(chat_id, message_id)

        elif call.data == "request_compensation":
            handle_compensation_request(call, chat_id, message_id)

        elif call.data.startswith("approve_compensation_"):
            handle_approve_compensation(call, chat_id, message_id)

        elif call.data.startswith("reject_compensation_"):
            handle_reject_compensation(call, chat_id, message_id)
        
        elif call.data == "compensation_admin":
            if is_admin(chat_id):
                show_compensation_admin_panel(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, text="❌ ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "edit_compensation_rate":
            if is_admin(chat_id):
                user_data[chat_id] = {'state': 'edit_compensation_rate'}
                bot.send_message(chat_id, "🛡️ أرسل نسبة التعويض الجديدة (بدون %):\n\n<em>مثال: 10 (لنسبة 10%)</em>", parse_mode="HTML")
            else:
                bot.answer_callback_query(call.id, text="❌ ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "edit_min_loss_amount":
            if is_admin(chat_id):
                user_data[chat_id] = {'state': 'edit_min_loss_amount'}
                bot.send_message(chat_id, "🛡️ أرسل الحد الأدنى للخسارة الجديد (SYP):\n\n<em>مثال: 10000</em>", parse_mode="HTML")
            else:
                bot.answer_callback_query(call.id, text="❌ ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "toggle_compensation":
            if is_admin(chat_id):
                toggle_compensation_system(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, text="❌ ليس لديك صلاحية الدخول", show_alert=True)
        
        elif call.data == "pending_compensations":
            if is_admin(chat_id):
                show_pending_compensations(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, text="❌ ليس لديك صلاحية الدخول", show_alert=True)
        elif call.data == "refund_last_withdrawal":
            handle_refund_last_withdrawal(call, chat_id, message_id)
        elif call.data.startswith("confirm_refund_"):
            withdrawal_id = call.data.replace("confirm_refund_", "")
            process_withdrawal_refund(call, withdrawal_id)
        
        elif call.data == "contact_support":
            start_contact_support(chat_id)
        
        elif call.data.startswith("reply_to_user_"):
            user_id = call.data.replace("reply_to_user_", "")
            start_admin_reply(call, user_id)

        elif call.data.startswith("close_support_"):
            user_id = call.data.replace("close_support_", "")
            close_support_request(call, user_id)
        
        elif call.data == "confirm_support_message":
            confirm_support_message(call)

        elif call.data == "cancel_support_message":
            cancel_support_message(call)
        elif call.data == "show_terms":
            show_terms_and_conditions(chat_id, message_id)
        elif call.data == "gift_balance":
            show_gift_section(chat_id, message_id)

        elif call.data == "start_gift":
            start_gift_process(chat_id)

        elif call.data == "gift_history":
            show_gift_history(chat_id, message_id)

        elif call.data == "confirm_gift":
            process_gift_transaction(chat_id)
            show_gift_section(chat_id, call.message.message_id)

        elif call.data == "cancel_gift":
            if chat_id in user_data:
                del user_data[chat_id]
            show_gift_section(chat_id, call.message.message_id)
            bot.answer_callback_query(call.id, "تم إلغاء العملية")
        
        # معالجات إدارة الإهداء
        elif call.data == "gift_admin":
            if is_admin(chat_id):
                show_gift_admin_panel(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, "ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "gift_detailed_stats":
            if is_admin(chat_id):
                show_gift_detailed_stats(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, "ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "all_gift_transactions":
            if is_admin(chat_id):
                show_all_gift_transactions(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, "ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "edit_gift_settings":
            if is_admin(chat_id):
                show_edit_gift_settings(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, "ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "edit_gift_commission":
            if is_admin(chat_id):
                start_edit_gift_commission(chat_id)
            else:
                bot.answer_callback_query(call.id, "ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "edit_gift_min_amount":
            if is_admin(chat_id):
                start_edit_gift_min_amount(chat_id)
            else:
                bot.answer_callback_query(call.id, "ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "toggle_gift_system":
            if is_admin(chat_id):
                toggle_gift_system(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, "ليس لديك صلاحية الدخول", show_alert=True)

        elif call.data == "export_gift_data":
            if is_admin(chat_id):
                export_gift_data(chat_id)
            else:
                bot.answer_callback_query(call.id, "ليس لديك صلاحية الدخول", show_alert=True)
        
        elif call.data == "gift_code":
            start_gift_code_input(chat_id)

        elif call.data == "gift_code_admin":
            if is_admin(chat_id):
                start_create_gift_code(chat_id)
            else:
                bot.answer_callback_query(call.id, "ليس لديك صلاحية", show_alert=True)
        
        elif call.data == "gift_code_manage":
            if is_admin(chat_id):
                show_gift_code_management(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, "ليس لديك صلاحية", show_alert=True)

        elif call.data.startswith("revoke_gift_"):
            if is_admin(chat_id):
                usage_id = call.data.replace("revoke_gift_", "")
                handle_revoke_gift_code(call, usage_id)
            else:
                bot.answer_callback_query(call.id, "ليس لديك صلاحية", show_alert=True)
        
        # في قسم handle_callbacks - إضافة المعالج الجديد
        elif call.data == "withdraw_history":
            show_withdraw_history(chat_id, message_id)

        elif call.data == "withdraw_stats":
            show_withdraw_stats(chat_id, message_id)
        
        
    except Exception as e:
        logger.error(f"❌ خطأ في المعالجة: {e}")
        bot.answer_callback_query(call.id, "حدث خطأ", show_alert=True)



@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'edit_points_per_10000')
def handle_edit_points_per_10000(message):
    chat_id = str(message.chat.id)
    try:
        points = int(message.text.strip())
        if points < 1:
            bot.send_message(chat_id, "يجب أن يكون العدد 1 على الأقل")
            return
        
        settings = load_loyalty_settings()
        settings['points_per_10000'] = str(points)
        save_loyalty_settings(settings)
        
        bot.send_message(chat_id, f"تم تحديث النقاط لكل 10,000 إلى {points}♞")
        
        # تنظيف البيانات
        if chat_id in user_data:
            del user_data[chat_id]
        
        # العودة للوحة الإدارة
        show_loyalty_settings_admin(chat_id, None)
        
    except ValueError:
        bot.send_message(chat_id, "يرجى إدخال رقم صحيح")

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'edit_referral_points')
def handle_edit_referral_points(message):
    chat_id = str(message.chat.id)
    try:
        points = int(message.text.strip())
        if points < 0:
            bot.send_message(chat_id, "يجب أن يكون العدد 0 على الأقل")
            return
        
        settings = load_loyalty_settings()
        settings['referral_points'] = str(points)
        save_loyalty_settings(settings)
        
        bot.send_message(chat_id, f"تم تحديث نقاط الإحالة إلى {points}♞")
        
        if chat_id in user_data:
            del user_data[chat_id]
        
        show_loyalty_settings_admin(chat_id, None)
        
    except ValueError:
        bot.send_message(chat_id, "يرجى إدخال رقم صحيح")

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'edit_deposit_bonus')
def handle_edit_deposit_bonus(message):
    chat_id = str(message.chat.id)
    try:
        points = int(message.text.strip())
        if points < 0:
            bot.send_message(chat_id, "يجب أن يكون العدد 0 على الأقل")
            return
        
        settings = load_loyalty_settings()
        settings['first_deposit_bonus'] = str(points)
        save_loyalty_settings(settings)
        
        bot.send_message(chat_id, f"تم تحديث مكافأة الإيداع الأولى إلى {points}♞")
        
        if chat_id in user_data:
            del user_data[chat_id]
        
        show_loyalty_settings_admin(chat_id, None)
        
    except ValueError:
        bot.send_message(chat_id, "يرجى إدخال رقم صحيح")

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'edit_min_redemption')
def handle_edit_min_redemption(message):
    chat_id = str(message.chat.id)
    try:
        points = int(message.text.strip())
        if points < 1:
            bot.send_message(chat_id, "يجب أن يكون العدد 1 على الأقل")
            return
        
        settings = load_loyalty_settings()
        settings['min_redemption_points'] = str(points)
        save_loyalty_settings(settings)
        
        bot.send_message(chat_id, f"تم تحديث الحد الأدنى للاستبدال إلى {points}♞")
        
        if chat_id in user_data:
            del user_data[chat_id]
        
        show_loyalty_settings_admin(chat_id, None)
        
    except ValueError:
        bot.send_message(chat_id, "يرجى إدخال رقم صحيح")

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'edit_reset_days')
def handle_edit_reset_days(message):
    chat_id = str(message.chat.id)
    try:
        days = int(message.text.strip())
        if days < 1:
            bot.send_message(chat_id, "يجب أن يكون العدد 1 على الأقل")
            return
        
        settings = load_loyalty_settings()
        settings['reset_days'] = str(days)
        save_loyalty_settings(settings)
        
        bot.send_message(chat_id, f"تم تحديث أيام التصفير إلى {days} يوم")
        
        if chat_id in user_data:
            del user_data[chat_id]
        
        show_loyalty_settings_admin(chat_id, None)
        
    except ValueError:
        bot.send_message(chat_id, "يرجى إدخال رقم صحيح")

@bot.callback_query_handler(func=lambda call: call.data.startswith('approve_redemption_'))
def handle_approve_redemption(call):
    chat_id = str(call.message.chat.id)
    message_id = call.message.message_id
    
    if not is_admin(chat_id):
        bot.answer_callback_query(call.id, text="ليس لديك صلاحية الموافقة", show_alert=True)
        return
    
    try:
        redemption_id = call.data.replace('approve_redemption_', '')
        
        # تحديث حالة الطلب
        success = db_manager.execute_query("""
            UPDATE loyalty_redemptions 
            SET status = 'approved', processed_at = CURRENT_TIMESTAMP 
            WHERE redemption_id = %s AND status = 'pending'
        """, (redemption_id,))
        
        if success:
            # جلب بيانات الطلب
            result = db_manager.execute_query("""
                SELECT lr.user_id, lr.points_cost, lrw.name as reward_name
                FROM loyalty_redemptions lr
                JOIN loyalty_rewards lrw ON lr.reward_id = lrw.reward_id
                WHERE lr.redemption_id = %s
            """, (redemption_id,))
            
            if result and len(result) > 0:
                user_id = result[0]['user_id']
                points_cost = result[0]['points_cost']
                reward_name = result[0]['reward_name']
                
                # إرسال إشعار للمستخدم
                try:
                    bot.send_message(
                        user_id,
                        f"""<b>🎉 تمت الموافقة على طلب استبدال النقاط</b>

🏆 <b>الجائزة:</b> {reward_name}
💎 <b>النقاط المستبدلة:</b> {points_cost}♞
⏰ <b>الوقت:</b> {datetime.now().strftime('%Y-%m-%d %H:%M')}

سيتم التواصل معك قريباً لتسليم الجائزة.""",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"خطأ في إرسال إشعار للمستخدم: {str(e)}")
            
            # تحديث رسالة المجموعة
            updated_text = f"""<b>✅ تمت الموافقة على طلب الاستبدال</b>

📋 <b>رقم الطلب:</b> <code>{redemption_id}</code>
⏰ <b>وقت المعالجة:</b> {datetime.now().strftime('%Y-%m-%d %H:%M')}
🔰 <b>الحالة:</b> مكتمل"""
            
            edit_group_message(call.message.chat.id, call.message.message_id, updated_text, None)
            bot.answer_callback_query(call.id, text="تمت الموافقة على الطلب")
        else:
            bot.answer_callback_query(call.id, text="فشل في معالجة الطلب", show_alert=True)
    
    except Exception as e:
        logger.error(f"خطأ في معالجة الموافقة: {str(e)}")
        bot.answer_callback_query(call.id, text="حدث خطأ في المعالجة", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith('reject_redemption_'))
def handle_reject_redemption(call):
    chat_id = str(call.message.chat.id)
    message_id = call.message.message_id
    
    if not is_admin(chat_id):
        bot.answer_callback_query(call.id, text="ليس لديك صلاحية الرفض", show_alert=True)
        return
    
    try:
        redemption_id = call.data.replace('reject_redemption_', '')
        
        # جلب بيانات الطلب أولاً
        result = db_manager.execute_query("""
            SELECT lr.user_id, lr.points_cost, lrw.name as reward_name
            FROM loyalty_redemptions lr
            JOIN loyalty_rewards lrw ON lr.reward_id = lrw.reward_id
            WHERE lr.redemption_id = %s
        """, (redemption_id,))
        
        if result and len(result) > 0:
            user_id = result[0]['user_id']
            points_cost = result[0]['points_cost']
            reward_name = result[0]['reward_name']
            
            # استرجاع النقاط للمستخدم
            db_manager.execute_query("""
                UPDATE loyalty_points 
                SET points = points + %s 
                WHERE user_id = %s
            """, (points_cost, user_id))
            
            # تسجيل في السجل
            db_manager.execute_query("""
                INSERT INTO loyalty_points_history 
                (user_id, points_change, reason)
                VALUES (%s, %s, %s)
            """, (user_id, points_cost, f"استرجاع نقاط - رفض طلب {reward_name}"))
        
        # تحديث حالة الطلب
        success = db_manager.execute_query("""
            UPDATE loyalty_redemptions 
            SET status = 'rejected', processed_at = CURRENT_TIMESTAMP 
            WHERE redemption_id = %s AND status = 'pending'
        """, (redemption_id,))
        
        if success:
            # إرسال إشعار للمستخدم
            try:
                bot.send_message(
                    user_id,
                    f"""<b>❌ تم رفض طلب استبدال النقاط</b>

🏆 <b>الجائزة:</b> {reward_name}
💎 <b>النقاط المسترجعة:</b> {points_cost}♞
⏰ <b>الوقت:</b> {datetime.now().strftime('%Y-%m-%d %H:%M')}

تم استرجاع نقاطك إلى رصيدك.""",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"خطأ في إرسال إشعار للمستخدم: {str(e)}")
            
            # تحديث رسالة المجموعة
            updated_text = f"""<b>❌ تم رفض طلب الاستبدال</b>

📋 <b>رقم الطلب:</b> <code>{redemption_id}</code>
⏰ <b>وقت المعالجة:</b> {datetime.now().strftime('%Y-%m-%d %H:%M')}
🔰 <b>الحالة:</b> مرفوض"""
            
            edit_group_message(call.message.chat.id, call.message.message_id, updated_text, None)
            bot.answer_callback_query(call.id, text="تم رفض الطلب واسترجاع النقاط")
        else:
            bot.answer_callback_query(call.id, text="فشل في معالجة الطلب", show_alert=True)
    
    except Exception as e:
        logger.error(f"خطأ في معالجة الرفض: {str(e)}")
        bot.answer_callback_query(call.id, text="حدث خطأ في المعالجة", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data == 'loyalty_stats')
def handle_loyalty_stats(call):
    chat_id = str(call.message.chat.id)
    message_id = call.message.message_id
    
    try:
        # إحصائيات عامة
        total_users_result = db_manager.execute_query("SELECT COUNT(*) as count FROM loyalty_points WHERE points > 0")
        total_users = total_users_result[0]['count'] if total_users_result else 0
        
        total_points_result = db_manager.execute_query("SELECT SUM(points) as total FROM loyalty_points")
        total_points = total_points_result[0]['total'] if total_points_result and total_points_result[0]['total'] else 0
        
        pending_requests_result = db_manager.execute_query("SELECT COUNT(*) as count FROM loyalty_redemptions WHERE status = 'pending'")
        pending_requests = pending_requests_result[0]['count'] if pending_requests_result else 0
        
        completed_requests_result = db_manager.execute_query("SELECT COUNT(*) as count FROM loyalty_redemptions WHERE status = 'approved'")
        completed_requests = completed_requests_result[0]['count'] if completed_requests_result else 0
        
        # أفضل 5 مستخدمين
        top_users = get_top_users_by_points(5)
        
        text = f"""
<b>📊 إحصائيات نظام نقاط الامتياز</b>

👥 <b>إجمالي المستخدمين النشطين:</b> {total_users}
💎 <b>إجمالي النقاط الموزعة:</b> {total_points}♞
📋 <b>طلبات الاستبدال المعلقة:</b> {pending_requests}
✅ <b>طلبات الاستبدال المكتملة:</b> {completed_requests}

🏆 <b>أفضل 5 مستخدمين:</b>
"""
        
        if top_users:
            for i, user in enumerate(top_users, 1):
                user_id = user['user_id']
                points = user['points']
                text += f"{i}. {user_id[:8]}... - {points}♞\n"
        else:
            text += "لا توجد بيانات\n"
        
        # إحصائيات الشهر الحالي
        current_month = datetime.now().strftime('%Y-%m')
        monthly_points_result = db_manager.execute_query("""
            SELECT SUM(points_change) as total 
            FROM loyalty_points_history 
            WHERE EXTRACT(YEAR_MONTH FROM created_at) = %s AND points_change > 0
        """, (current_month.replace('-', ''),))
        
        monthly_points = monthly_points_result[0]['total'] if monthly_points_result and monthly_points_result[0]['total'] else 0
        
        text += f"\n📈 <b>نقاط الشهر الحالي:</b> {monthly_points}♞"
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔄 تحديث", callback_data="loyalty_stats"))
        markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="loyalty_admin"))
        
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )
        
    except Exception as e:
        logger.error(f"خطأ في عرض الإحصائيات: {str(e)}")
        bot.answer_callback_query(call.id, text="حدث خطأ في عرض الإحصائيات", show_alert=True)


@bot.callback_query_handler(func=lambda call: call.data == 'manage_rewards')
def handle_manage_rewards(call):
    chat_id = str(call.message.chat.id)
    message_id = call.message.message_id
    
    if not is_admin(chat_id):
        bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)
        return
    
    show_rewards_management(chat_id, message_id)

def show_rewards_management(chat_id, message_id):
    """عرض إدارة الجوائز"""
    rewards = get_loyalty_rewards()
    
    text = "<b>🎁 إدارة الجوائز</b>\n\n"
    
    if rewards:
        for reward_id, reward in rewards.items():
            status = "✅ مفعل" if reward['active'] else "❌ معطل"
            text += f"<b>{reward['name']}</b>\n"
            text += f"   التكلفة: {reward['points_cost']}♞\n"
            text += f"   الخصم: {reward['discount_rate']}%\n"
            text += f"   الحالة: {status}\n\n"
    else:
        text += "لا توجد جوائز\n"
    
    text += "اختر الإجراء المطلوب:"
    
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("➕ إضافة جائزة", callback_data="add_reward"),
        types.InlineKeyboardButton("🔄 تحديث", callback_data="manage_rewards")
    )
    
    if rewards:
        for reward_id, reward in list(rewards.items())[:5]:  # عرض أول 5 جوائز فقط
            markup.row(
                types.InlineKeyboardButton(f"✏️ {reward['name']}", callback_data=f"edit_reward_{reward_id}"),
                types.InlineKeyboardButton(f"❌ {reward['name']}", callback_data=f"toggle_reward_{reward_id}")
            )
    
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="loyalty_admin"))
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        parse_mode="HTML",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('toggle_reward_'))
def handle_toggle_reward(call):
    chat_id = str(call.message.chat.id)
    message_id = call.message.message_id
    
    if not is_admin(chat_id):
        bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)
        return
    
    try:
        reward_id = call.data.replace('toggle_reward_', '')
        
        # جلب الحالة الحالية
        result = db_manager.execute_query(
            "SELECT active FROM loyalty_rewards WHERE reward_id = %s",
            (reward_id,)
        )
        
        if result and len(result) > 0:
            current_status = result[0]['active']
            new_status = not current_status
            
            # تحديث الحالة
            success = db_manager.execute_query(
                "UPDATE loyalty_rewards SET active = %s WHERE reward_id = %s",
                (new_status, reward_id)
            )
            
            if success:
                status_text = "مفعل" if new_status else "معطل"
                bot.answer_callback_query(call.id, text=f"تم {status_text} الجائزة")
                show_rewards_management(chat_id, message_id)
            else:
                bot.answer_callback_query(call.id, text="فشل في تحديث الجائزة", show_alert=True)
        else:
            bot.answer_callback_query(call.id, text="الجائزة غير موجودة", show_alert=True)
    
    except Exception as e:
        logger.error(f"خطأ في تبديل حالة الجائزة: {str(e)}")
        bot.answer_callback_query(call.id, text="حدث خطأ في المعالجة", show_alert=True)


@bot.callback_query_handler(func=lambda call: call.data == 'add_reward')
def handle_add_reward(call):
    chat_id = str(call.message.chat.id)
    
    if not is_admin(chat_id):
        bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)
        return
    
    user_data[chat_id] = {'state': 'add_reward_name'}
    bot.send_message(
        chat_id,
        "<b>إضافة جائزة جديدة</b>\n\nالخطوة 1/4: أرسل اسم الجائزة\nمثال: <em>10$</em>",
        parse_mode="HTML",
        reply_markup=EnhancedKeyboard.create_back_button("manage_rewards")
    )

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'add_reward_name')
def handle_add_reward_name(message):
    chat_id = str(message.chat.id)
    name = message.text.strip()
    
    if len(name) < 2:
        bot.send_message(chat_id, "يجب أن يكون الاسم حرفين على الأقل")
        return
    
    user_data[chat_id]['reward_name'] = name
    user_data[chat_id]['state'] = 'add_reward_description'
    
    bot.send_message(
        chat_id,
        "الخطوة 2/4: أرسل وصف الجائزة (اختياري)\nمثال: <em>رصيد 10 دولار</em>",
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'add_reward_description')
def handle_add_reward_description(message):
    chat_id = str(message.chat.id)
    description = message.text.strip()
    
    user_data[chat_id]['reward_description'] = description
    user_data[chat_id]['state'] = 'add_reward_points'
    
    bot.send_message(
        chat_id,
        "الخطوة 3/4: أرسل تكلفة الجائزة بالنقاط\nمثال: <em>250</em>",
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'add_reward_points')
def handle_add_reward_points(message):
    chat_id = str(message.chat.id)
    
    try:
        points = int(message.text.strip())
        if points < 1:
            bot.send_message(chat_id, "يجب أن يكون العدد 1 على الأقل")
            return
        
        user_data[chat_id]['reward_points'] = points
        user_data[chat_id]['state'] = 'add_reward_discount'
        
        bot.send_message(
            chat_id,
            "الخطوة 4/4: أرسل نسبة الخصم (0 إذا لم يكن هناك خصم)\nمثال: <em>10</em> للخصم 10%",
            parse_mode="HTML"
        )
    
    except ValueError:
        bot.send_message(chat_id, "يرجى إدخال رقم صحيح")

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'add_reward_discount')
def handle_add_reward_discount(message):
    chat_id = str(message.chat.id)
    
    try:
        discount = float(message.text.strip())
        if discount < 0 or discount > 100:
            bot.send_message(chat_id, "يجب أن تكون النسبة بين 0 و 100")
            return
        
        # حفظ الجائزة
        reward_id = f"reward_{int(time.time() * 1000)}"
        name = user_data[chat_id]['reward_name']
        description = user_data[chat_id]['reward_description']
        points = user_data[chat_id]['reward_points']
        
        success = db_manager.execute_query("""
            INSERT INTO loyalty_rewards 
            (reward_id, name, description, points_cost, discount_rate)
            VALUES (%s, %s, %s, %s, %s)
        """, (reward_id, name, description, points, discount))
        
        if success:
            bot.send_message(chat_id, f"✅ تم إضافة الجائزة '{name}' بنجاح")
        else:
            bot.send_message(chat_id, "❌ فشل في إضافة الجائزة")
        
        # تنظيف البيانات
        if chat_id in user_data:
            del user_data[chat_id]
        
        # العودة للقائمة
        show_rewards_management(chat_id, None)
    
    except ValueError:
        bot.send_message(chat_id, "يرجى إدخال رقم صحيح")

@bot.callback_query_handler(func=lambda call: call.data == 'reset_all_points')
def handle_reset_all_points(call):
    chat_id = str(call.message.chat.id)
    
    if not is_admin(chat_id):
        bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)
        return
    
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("✅ نعم، تصفير الكل", callback_data="confirm_reset_all_points"),
        types.InlineKeyboardButton("❌ إلغاء", callback_data="loyalty_admin")
    )
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text="<b>⚠️ تأكيد تصفير جميع النقاط</b>\n\nهل أنت متأكد من تصفير جميع نقاط المستخدمين؟ هذا الإجراء لا يمكن التراجع عنه.",
        parse_mode="HTML",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data == 'confirm_reset_all_points')
def handle_confirm_reset_all_points(call):
    chat_id = str(call.message.chat.id)
    
    if not is_admin(chat_id):
        bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)
        return
    
    try:
        # تصفير جميع النقاط
        success = db_manager.execute_query("UPDATE loyalty_points SET points = 0, last_reset = CURRENT_TIMESTAMP")
        
        if success:
            # تسجيل في السجل
            db_manager.execute_query("""
                INSERT INTO loyalty_points_history (user_id, points_change, reason)
                SELECT user_id, -points, 'تصفير جماعي من الإدارة'
                FROM loyalty_points
                WHERE points > 0
            """)
            
            bot.answer_callback_query(call.id, text="تم تصفير جميع النقاط بنجاح")
            show_loyalty_admin_panel(chat_id, call.message.message_id)
        else:
            bot.answer_callback_query(call.id, text="فشل في تصفير النقاط", show_alert=True)
    
    except Exception as e:
        logger.error(f"خطأ في تصفير النقاط: {str(e)}")
        bot.answer_callback_query(call.id, text="حدث خطأ في التصفير", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data == 'export_points_data')
def handle_export_points_data(call):
    chat_id = str(call.message.chat.id)
    
    if not is_admin(chat_id):
        bot.answer_callback_query(call.id, text="ليس لديك صلاحية الدخول", show_alert=True)
        return
    
    try:
        # جلب بيانات النقاط
        result = db_manager.execute_query("""
            SELECT user_id, points, last_reset, updated_at
            FROM loyalty_points 
            WHERE points > 0 
            ORDER BY points DESC
        """)
        
        if result:
            # إنشاء ملف نصي
            csv_data = "User ID,Points,Last Reset,Last Update\n"
            for row in result:
                csv_data += f"{row['user_id']},{row['points']},{row['last_reset']},{row['updated_at']}\n"
            
            # إرسال كملف
            bot.send_document(
                chat_id,
                ('loyalty_points.csv', csv_data.encode('utf-8')),
                caption="<b>📊 تصدير بيانات نقاط الامتياز</b>",
                parse_mode="HTML"
            )
            bot.answer_callback_query(call.id, text="تم تصدير البيانات")
        else:
            bot.answer_callback_query(call.id, text="لا توجد بيانات للتصدير", show_alert=True)
    
    except Exception as e:
        logger.error(f"خطأ في تصدير البيانات: {str(e)}")
        bot.answer_callback_query(call.id, text="حدث خطأ في التصدير", show_alert=True)







# ===============================================================
# دوال الواجهة الرئيسية
# ===============================================================

def show_main_menu(chat_id, message_id):
    accounts = load_accounts()
    has_account = str(chat_id) in accounts
    
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="<b>القائمة الرئيسية</b>\n\nاختر من الخيارات:",
            parse_mode="HTML",
            reply_markup=EnhancedKeyboard.create_main_menu(has_account, is_admin(chat_id))
        )
    except:
        bot.send_message(
            chat_id,
            "<b>القائمة الرئيسية</b>\n\nاختر من الخيارات:",
            parse_mode="HTML",
            reply_markup=EnhancedKeyboard.create_main_menu(has_account, is_admin(chat_id))
        )

def show_account_section(chat_id, message_id):
    accounts = load_accounts()
    has_account = str(chat_id) in accounts
    
    text = "<b>⚡ قسم حساب 55BETS</b>\n\n"
    
    if has_account:
        account = accounts[str(chat_id)]
        player_id = account.get("playerId")
        account_balance = get_player_balance_via_agent(player_id) if player_id else 'غير متوفر'
        wallet_balance = get_wallet_balance(chat_id)
        
        text += f"""<b>✅ معلومات  تسجيل الدخول لحسابك في bets55
</b>
<blockquote>👤 <b>اسم المستخدم:</b> </blockquote> <code>{account.get('username', 'غير محدد')}</code>
<blockquote>💰 <b>رصيد الحساب:</b> </blockquote> <code>{account_balance}</code>
<blockquote>💳 <b>رصيد المحفظة:</b> </blockquote> <code>{wallet_balance}</code>

اختر الإجراء المطلوب:"""
    else:
        text += "❌ ليس لديك حساب بعد\n\nأنشئ حسابك الآن للبدء:"
    
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=EnhancedKeyboard.create_account_section(has_account)
        )
    except:
        bot.send_message(
            chat_id,
            text,
            parse_mode="HTML",
            reply_markup=EnhancedKeyboard.create_account_section(has_account)
        )


def handle_subscription_check(call, chat_id, message_id):
    if is_user_subscribed(call.from_user.id):
        accounts = load_accounts()
        has_account = str(chat_id) in accounts
        
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="<b>القائمة الرئيسية</b>\n\nاختر من القائمة:",
            parse_mode="HTML",
            reply_markup=EnhancedKeyboard.create_main_menu(has_account, is_admin(chat_id))
        )
    else:
        bot.answer_callback_query(call.id, "لم تشترك في القناة بعد", show_alert=True)

def start_account_creation(chat_id):
    user_data[chat_id] = {'state': 'awaiting_username'}
    bot.send_message(
        chat_id,
        """<b>🆕 إنشاء حساب جديد</b>

الخطوة 1/2: أرسل اسم المستخدم المطلوب
<em>سيتم إضافة لاحقة عشوائية تلقائيًا</em>""",
        parse_mode="HTML",
        reply_markup=EnhancedKeyboard.create_back_button("account_section")
    )

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'awaiting_username')
def handle_username_input(message):
    chat_id = str(message.chat.id)
    username = message.text.strip()
    
    if len(username) < 3:
        bot.send_message(chat_id, "❌ اسم المستخدم يجب أن يكون 3 أحرف على الأقل")
        return
    
    user_data[chat_id] = {
        'state': 'awaiting_password',
        'username': username
    }
    
    bot.send_message(
        chat_id,
        """الخطوة 2/2: أرسل كلمة المرور المطلوبة
<em>يجب أن تكون قوية ويصعب تخمينها</em>""",
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'awaiting_password')
def handle_password_input(message):
    chat_id = str(message.chat.id)
    password = message.text.strip()
    
    if len(password) < 4:
        bot.send_message(chat_id, "❌ كلمة المرور يجب أن تكون 4 أحرف على الأقل")
        return
    
    username = user_data[chat_id]['username']
    
    # إضافة المهمة إلى الطابور
    task = {
        'type': 'create_account',
        'chat_id': chat_id,
        'username': username,
        'password': password
    }
    account_operations_queue.put(task)
    
    # تنظيف البيانات المؤقتة
    if chat_id in user_data:
        del user_data[chat_id]
    
    bot.send_message(
        chat_id,
        """<b>⏳ جاري إنشاء الحساب...</b>

قد تستغرق العملية بضع ثوانٍ
ستتلقى إشعاراً عند اكتمال العملية""",
        parse_mode="HTML",
        reply_markup=EnhancedKeyboard.create_back_button("account_section")
    )

def show_account_info(chat_id, message_id):
    accounts = load_accounts()
    account = accounts.get(str(chat_id))
    
    if account:
        player_id = account.get("playerId")
        balance = get_player_balance_via_agent(player_id) if player_id else 'غير متوفر'
        wallet_balance = get_wallet_balance(chat_id)
        
        account_info = f"""
<b>👤 معلومات الحساب</b>

👤 <b>اسم المستخدم:</b> <code>{account.get('username', 'غير محدد')}</code>
🔐 <b>كلمة المرور:</b> <code>{account.get('password', 'غير محدد')}</code>
🆔 <b>رقم اللاعب:</b> <code>{player_id if player_id else 'غير محدد'}</code>
💰 <b>رصيد الحساب:</b> <code>{balance}</code>
💳 <b>رصيد المحفظة:</b> <code>{wallet_balance}</code>
        """
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="account_section"))
        
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=account_info,
                parse_mode="HTML",
                reply_markup=markup
            )
        except:
            bot.send_message(
                chat_id,
                account_info,
                parse_mode="HTML",
                reply_markup=markup
            )
    else:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="❌ لا يوجد حساب مرتبط بك",
            parse_mode="HTML",
            reply_markup=EnhancedKeyboard.create_back_button("account_section")
        )


def start_deposit_to_account(chat_id):
    accounts = load_accounts()
    account = accounts.get(str(chat_id))
    
    if not account:
        bot.send_message(chat_id, "❌ لا يوجد حساب مرتبط بك")
        return
    
    player_id = account.get("playerId")
    if not player_id:
        bot.send_message(chat_id, "❌ لم يتم العثور على معرف اللاعب")
        return
    
    user_data[chat_id] = {
        'state': 'deposit_to_account_amount',
        'player_id': player_id
    }
    
    bot.send_message(
        chat_id,
        """<b>↙️ شحن الحساب</b>

أرسل المبلغ المراد شحنه
<em>سيتم خصم المبلغ من رصيد محفظتك</em>""",
        parse_mode="HTML",
        reply_markup=EnhancedKeyboard.create_back_button("account_section")
    )

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'deposit_to_account_amount')
def handle_deposit_to_account_amount(message):
    chat_id = str(message.chat.id)
    
    try:
        amount = float(message.text.strip())
        wallet_balance = get_wallet_balance(chat_id)
        
        if wallet_balance < amount:
            bot.send_message(chat_id, f"❌ رصيدك غير كافي. رصيدك الحالي: {wallet_balance}")
            return
        
        player_id = user_data[chat_id]['player_id']
        
        task = {
            'type': 'deposit_to_account',
            'chat_id': chat_id,
            'amount': amount,
            'player_id': player_id
        }
        account_operations_queue.put(task)
        
        if chat_id in user_data:
            del user_data[chat_id]
        
        bot.send_message(
            chat_id,
            "<b>🔄 جاري معالجة طلب الشحن...</b>\n⏳ قد تستغرق العملية بضع ثوانٍ",
            parse_mode="HTML"
        )
        
    except ValueError:
        bot.send_message(chat_id, "❌ يرجى إدخال مبلغ صحيح")

def start_withdraw_from_account(chat_id):
    accounts = load_accounts()
    account = accounts.get(str(chat_id))
    
    if not account:
        bot.send_message(chat_id, "❌ لا يوجد حساب مرتبط بك")
        return
    
    player_id = account.get("playerId")
    if not player_id:
        bot.send_message(chat_id, "❌ لم يتم العثور على معرف اللاعب")
        return
    
    account_balance = get_player_balance_via_agent(player_id)
    
    user_data[chat_id] = {
        'state': 'withdraw_from_account_amount',
        'player_id': player_id
    }
    
    bot.send_message(
        chat_id,
        f"""<b>↗️ السحب من الحساب</b>

رصيد حسابك: <b>{account_balance}</b>
أرسل المبلغ المراد سحبه
<em>سيتم إضافة المبلغ إلى رصيد محفظتك</em>""",
        parse_mode="HTML",
        reply_markup=EnhancedKeyboard.create_back_button("account_section")
    )

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'withdraw_from_account_amount')
def handle_withdraw_from_account_amount(message):
    chat_id = str(message.chat.id)
    
    try:
        amount = float(message.text.strip())
        player_id = user_data[chat_id]['player_id']
        
        account_balance = get_player_balance_via_agent(player_id)
        if account_balance < amount:
            bot.send_message(chat_id, f"❌ رصيد حسابك غير كافي. رصيدك الحالي: {account_balance}")
            return
        
        task = {
            'type': 'withdraw_from_account',
            'chat_id': chat_id,
            'amount': amount,
            'player_id': player_id
        }
        account_operations_queue.put(task)
        
        if chat_id in user_data:
            del user_data[chat_id]
        
        bot.send_message(
            chat_id,
            "<b>🔄 جاري معالجة طلب السحب...</b>\n⏳ قد تستغرق العملية بضع ثوانٍ",
            parse_mode="HTML"
        )
        
    except ValueError:
        bot.send_message(chat_id, "❌ يرجى إدخال مبلغ صحيح")

def show_payment_methods(chat_id, message_id):
    methods = payment_system.get_active_methods()
    
    if methods:
        methods_text = "<b>📥 طرق الدفع المتاحة</b>\n\n"
        for method_id, method in methods.items():
            methods_text += f"• {method['name']}\n"
        
        methods_text += "\n<b>اختر طريقة الدفع المناسبة لك:</b>"
        
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=methods_text,
            parse_mode="HTML",
            reply_markup=payment_system.get_method_buttons("payment")
        )
    else:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="<b>❌ لا توجد طرق دفع متاحة حالياً</b>",
            parse_mode="HTML",
            reply_markup=EnhancedKeyboard.create_back_button()
        )

def show_withdraw_methods(chat_id, message_id):
    methods = withdraw_system.get_active_methods()
    
    if methods:
        methods_text = "<b>📤 طرق السحب المتاحة</b>\n\n"
        for method_id, method in methods.items():
            methods_text += f"• {method['name']}\n"
        
        methods_text += "\n<b>اختر طريقة السحب المناسبة لك:</b>"
        
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=methods_text,
            parse_mode="HTML",
            reply_markup=withdraw_system.get_method_buttons()
        )
    else:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="<b>❌ لا توجد طرق سحب متاحة حالياً</b>",
            parse_mode="HTML",
            reply_markup=EnhancedKeyboard.create_back_button()
        )

def show_balance_history(chat_id, message_id):
    wallet_balance = get_wallet_balance(chat_id)
    accounts = load_accounts()
    account = accounts.get(str(chat_id))
    
    account_balance = 0
    if account and account.get('playerId'):
        account_balance = get_player_balance_via_agent(account.get('playerId'))
    
    balance_text = f"""
<b>📊 سجل الرصيد</b>

💳 <b>رصيد المحفظة:</b> <code>{wallet_balance}</code>
💰 <b>رصيد حساب 55BETS:</b> <code>{account_balance}</code>
📈 <b>إجمالي الرصيد:</b> <code>{wallet_balance + account_balance}</code>

<em>آخر تحديث: {time.strftime('%Y-%m-%d %H:%M:%S')}</em>
    """
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=balance_text,
        parse_mode="HTML",
        reply_markup=EnhancedKeyboard.create_back_button()
    )

def start_payment_process(chat_id, message_id, method_id):
    method = payment_system.methods.get(method_id)
    if not method:
        bot.answer_callback_query(message_id, "❌ طريقة الدفع غير موجودة")
        return
    
    user_data[chat_id] = {
        'state': 'payment_transaction_id',
        'payment_method': method_id
    }
    
    payment_info = f"""
<b>💳 عملية دفع عبر {method['name']}</b>

<b>العنوان:</b>
<code>{method['address']}</code>

<b>الحد الأدنى:</b> <b>{method['min_amount']}</b>
<b>سعر الصرف:</b> <b>{method['exchange_rate']}</b>

<b>بعد التمويل، أرسل رقم العملية:</b>
    """
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=payment_info,
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'payment_transaction_id')
def handle_payment_transaction_id(message):
    chat_id = str(message.chat.id)
    transaction_id = message.text.strip()
    
    user_data[chat_id]['transaction_id'] = transaction_id
    user_data[chat_id]['state'] = 'payment_amount'
    
    bot.send_message(
        chat_id,
        "<b>أرسل المبلغ الذي تم تحويله:</b>",
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'payment_amount')
def handle_payment_amount(message):
    chat_id = str(message.chat.id)
    
    try:
        amount = float(message.text.strip())
        method_id = user_data[chat_id]['payment_method']
        transaction_id = user_data[chat_id]['transaction_id']
        method = payment_system.methods.get(method_id)
        
        if amount < method['min_amount']:
            bot.send_message(chat_id, f"❌ المبلغ أقل من الحد الأدنى ({method['min_amount']})")
            return
        
        exchange_rate = method.get('exchange_rate', 1.0)
        final_amount = amount * exchange_rate
        
        if chat_id in user_data:
            del user_data[chat_id]
        
        request_text = f"""
<b>💳 طلب دفع جديد</b>

<b>المستخدم:</b> <code>{chat_id}</code>
<b>الطريقة:</b> {method['name']}
<b>المبلغ المحول:</b> {amount}
<b>سعر الصرف:</b> {exchange_rate}
<b>المبلغ المضاف:</b> {final_amount}
<b>رقم العملية:</b> <code>{transaction_id}</code>
<b>الوقت:</b> {time.strftime("%Y-%m-%d %H:%M:%S")}
        """
        
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton("✅ موافق", callback_data=f"approve_payment_{chat_id}_{final_amount}_{transaction_id}"),
            types.InlineKeyboardButton("❌ رفض", callback_data=f"reject_payment_{chat_id}_{transaction_id}")
        )
        
        group_message_id = send_to_payment_group(request_text, markup)
        group_chat_id = PAYMENT_REQUESTS_CHAT_ID if PAYMENT_REQUESTS_CHAT_ID else ADMIN_CHAT_ID
        
        add_payment_request(chat_id, final_amount, method_id, transaction_id, group_message_id, group_chat_id)
        
        bot.send_message(
            chat_id,
            """<b>✅ تم إرسال طلبك بنجاح</b>

تم إرسال طلبك للإدارة وسيتم الرد قريباً""",
            parse_mode="HTML"
        )
        
    except ValueError:
        bot.send_message(chat_id, "❌ يرجى إدخال مبلغ صحيح")

def is_payment_request_processed(user_id, transaction_id):
    """التحقق إذا كان طلب الدفع تم معالجته مسبقاً"""
    result = db_manager.execute_query(
        "SELECT status FROM payment_requests WHERE user_id = %s AND transaction_id = %s",
        (str(user_id), transaction_id)
    )
    if result and len(result) > 0:
        status = result[0]['status']
        return status != 'pending'
    return False

def is_withdrawal_processed(withdrawal_id):
    """التحقق إذا كان طلب السحب تم معالجته مسبقاً"""
    result = db_manager.execute_query(
        "SELECT status FROM pending_withdrawals WHERE withdrawal_id = %s",
        (withdrawal_id,)
    )
    if result and len(result) > 0:
        status = result[0]['status']
        return status != 'pending'
    return False



def start_withdraw_process(chat_id, method_id):
    method = withdraw_system.methods.get(method_id)
    if not method:
        bot.send_message(chat_id, "❌ طريقة السحب غير موجودة")
        return
    
    user_data[chat_id] = {
        'state': 'withdraw_amount',
        'withdraw_method': method_id
    }
    
    bot.send_message(
        chat_id,
        f"""<b>💸 سحب عبر {method['name']}</b>

أرسل المبلغ المراد سحبه
<em>عمولة السحب: {method['commission_rate']*100}%</em>""",
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'withdraw_amount')
def handle_withdraw_amount(message):
    chat_id = str(message.chat.id)
    
    try:
        amount = float(message.text.strip())
        method_id = user_data[chat_id]['withdraw_method']
        method = withdraw_system.methods.get(method_id)
        
        commission = amount * method['commission_rate']
        net_amount = amount - commission
        
        wallet_balance = get_wallet_balance(chat_id)
        if wallet_balance < amount:
            bot.send_message(chat_id, f"❌ رصيدك غير كافي. رصيدك الحالي: {wallet_balance}")
            return
        
        user_data[chat_id]['withdraw_amount'] = amount
        user_data[chat_id]['commission'] = commission
        user_data[chat_id]['net_amount'] = net_amount
        user_data[chat_id]['state'] = 'withdraw_address'
        
        bot.send_message(
            chat_id,
            f"""<b>💸 تأكيد بيانات السحب</b>

المبلغ المطلوب: <b>{amount}</b>
العمولة: <b>{commission}</b> ({method['commission_rate']*100}%)
المبلغ الصافي: <b>{net_amount}</b>

<b>أرسل عنوان السحب:</b>""",
            parse_mode="HTML"
        )
        
    except ValueError:
        bot.send_message(chat_id, "❌ يرجى إدخال مبلغ صحيح")

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'withdraw_address')
def handle_withdraw_address(message):
    chat_id = str(message.chat.id)
    address = message.text.strip()
    
    if len(address) < 5:
        bot.send_message(chat_id, "❌ العنوان يجب أن يكون 5 أحرف على الأقل")
        return
    
    amount = user_data[chat_id]['withdraw_amount']
    commission = user_data[chat_id]['commission']
    net_amount = user_data[chat_id]['net_amount']
    method_id = user_data[chat_id]['withdraw_method']
    method = withdraw_system.methods.get(method_id)
    
    current_balance = get_wallet_balance(chat_id)
    if current_balance >= amount:
        new_balance = update_wallet_balance(chat_id, -amount)
        
        request_text = f"""
<b>💸 طلب سحب جديد</b>

<b>المستخدم:</b> <code>{chat_id}</code>
<b>الطريقة:</b> {method['name']}
<b>المبلغ المطلوب:</b> {amount}
<b>العمولة:</b> {commission}
<b>المبلغ الصافي:</b> {net_amount}
<b>العنوان:</b> <code>{address}</code>
<b>الوقت:</b> {time.strftime('%Y-%m-%d %H:%M:%S')}
        """
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("✅ تم التنفيذ", callback_data=f"complete_withdraw_{chat_id}_{amount}"))
        
        group_message_id = send_to_withdraw_group(request_text, markup)
        group_chat_id = WITHDRAWAL_REQUESTS_CHAT_ID if WITHDRAWAL_REQUESTS_CHAT_ID else ADMIN_CHAT_ID
        
        withdrawal_id = add_pending_withdrawal(chat_id, amount, method_id, address, group_message_id, group_chat_id)
        
        updated_markup = types.InlineKeyboardMarkup()
        updated_markup.add(types.InlineKeyboardButton("✅ تم التنفيذ", callback_data=f"complete_withdraw_{withdrawal_id}"))
        
        if group_message_id and group_chat_id:
            edit_group_message(group_chat_id, group_message_id, request_text, updated_markup)
        
        bot.send_message(
            chat_id,
            f"""<b>✅ تم إرسال طلب السحب بنجاح</b>

المبلغ المخصوم: <b>{amount}</b>
رصيدك الحالي: <b>{new_balance}</b>

سيتم تنفيذ طلبك بأسرع وقت ممكن""",
            parse_mode="HTML"
        )
    else:
        bot.send_message(chat_id, "❌ رصيدك غير كافي لإتمام العملية")
    
    if chat_id in user_data:
        del user_data[chat_id]


def show_loyalty_section(chat_id, message_id):
    """عرض قسم نقاط الامتياز"""
    user_points = get_loyalty_points(chat_id)
    settings = load_loyalty_settings()
    
    text = f"""
<b>🎖 نظام نقاط الامتياز</b>

📊 <b>نقاطك الحالية:</b> <code>{user_points}♞</code>
🔄 <b>تصفير النقاط:</b> كل {settings.get('reset_days', 30)} يوم
💰 <b>الحد الأدنى للاستبدال:</b> {settings.get('min_redemption_points', 100)}♞

💡 <b>كيفية جمع النقاط:</b>
• شحن 10,000 = 1♞
• إحالة جديدة = {settings.get('referral_points', 1)}♞
• أول إيداع للمحيل = {settings.get('first_deposit_bonus', 3)}♞ إضافية

اختر الإجراء المطلوب:
"""
    
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("🏆 ترتيب أفضل 10", callback_data="loyalty_leaderboard"),
        types.InlineKeyboardButton("🎁 تبديل النقاط", callback_data="loyalty_redeem")
    )
    markup.row(
        types.InlineKeyboardButton("📊 سجل النقاط", callback_data="loyalty_history"),
        types.InlineKeyboardButton("🔄 تحديث", callback_data="loyalty_section")
    )
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="main_menu"))
    
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )
    except:
        bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )

def show_loyalty_leaderboard(chat_id, message_id):
    """عرض ترتيب أفضل 10"""
    top_users = get_top_users_by_points(10)
    user_points = get_loyalty_points(chat_id)
    user_rank = "غير محدد"
    
    text = f"""
<b>🏆 ترتيب أفضل 10 في نقاط الامتياز</b>

"""
    
    if top_users:
        for i, user in enumerate(top_users, 1):
            user_id = user['user_id']
            points = user['points']
            text += f"{i}. 👤 {user_id[:8]}... - {points}♞\n"
            
            if user_id == str(chat_id):
                user_rank = i
    else:
        text += "لا توجد نقاط مسجلة بعد\n"
    
    text += f"\n📊 <b>ترتيبك:</b> {user_rank}"
    text += f"\n🎯 <b>نقاطك:</b> {user_points}♞"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="loyalty_section"))
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        parse_mode="HTML",
        reply_markup=markup
    )

def show_loyalty_redeem(chat_id, message_id):
    """عرض واجهة استبدال النقاط"""
    user_points = get_loyalty_points(chat_id)
    settings = load_loyalty_settings()
    rewards = get_loyalty_rewards()
    
    text = f"""
<b>🎁 تبديل نقاط الامتياز</b>

📊 <b>نقاطك الحالية:</b> <code>{user_points}♞</code>
"""
    
    if settings.get('redemption_enabled') != 'true':
        text += "\n❌ <b>التبديل غير متاح حالياً</b>\n\n"
        text += "💡 ستتمكن من استبدال النقاط عند تفعيل التبديل من قبل الإدارة وسيتم إعلام جميع المستخدمين على البوت"
    else:
        min_points = int(settings.get('min_redemption_points', 100))
        if user_points < min_points:
            text += f"\n❌ <b>التبديل غير متاح</b>\n\n"
            text += f"الحد الأدنى للتبديل هو {min_points}♞"
        else:
            text += f"\n✅ <b>التبديل متاح</b>\n\n"
            text += "<b>الجوائز المتاحة للاستبدال بنقاط الامتياز:</b>\n\n"
    
    # عرض الجوائز
    for reward_id, reward in rewards.items():
        original_cost = reward['points_cost']
        discount = reward['discount_rate']
        
        if discount > 0:
            final_cost = original_cost * (1 - discount/100)
            final_cost = int(final_cost)
            text += f"☑️ {reward['name']}\n"
            text += f"   التكلفة: {original_cost:,}♞ {final_cost:,}♞ (خصم {discount}%)\n\n"
        else:
            text += f"☑️ {reward['name']}\n"
            text += f"   التكلفة: {original_cost:,}♞\n\n"
    
    markup = types.InlineKeyboardMarkup()
    
    if settings.get('redemption_enabled') == 'true' and user_points >= int(settings.get('min_redemption_points', 100)):
        # أزرار الجوائز
        buttons_per_row = 2
        reward_list = list(rewards.items())
        
        for i in range(0, len(reward_list), buttons_per_row):
            row_buttons = []
            for j in range(buttons_per_row):
                if i + j < len(reward_list):
                    reward_id, reward = reward_list[i + j]
                    original_cost = reward['points_cost']
                    discount = reward['discount_rate']
                    
                    if discount > 0:
                        final_cost = int(original_cost * (1 - discount/100))
                        button_text = f"{reward['name']} - {final_cost}♞"
                    else:
                        button_text = f"{reward['name']} - {original_cost}♞"
                    
                    # اختصار النص إذا كان طويلاً
                    if len(button_text) > 15:
                        button_text = f"{reward['name'][:10]}.. - {original_cost}♞"
                    
                    row_buttons.append(types.InlineKeyboardButton(
                        button_text,
                        callback_data=f"redeem_{reward_id}"
                    ))
            
            if row_buttons:
                markup.row(*row_buttons)
    
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="loyalty_section"))
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        parse_mode="HTML",
        reply_markup=markup
    )

def show_loyalty_history(chat_id, message_id):
    """عرض سجل النقاط"""
    history = get_user_redemption_history(chat_id)
    user_points = get_loyalty_points(chat_id)
    
    text = f"""
<b>📊 سجل نقاط الامتياز</b>

📈 <b>نقاطك الحالية:</b> <code>{user_points}♞</code>

<b>آخر العمليات:</b>
"""
    
    if history:
        for record in history[:10]:  # آخر 10 عمليات
            reward_name = record['reward_name']
            points_cost = record['points_cost']
            status = record['status']
            date = record['created_at'].strftime('%Y-%m-%d %H:%M')
            
            if status == 'pending':
                status_text = "⏳ قيد الانتظار"
            elif status == 'approved':
                status_text = "✅ مكتمل"
            elif status == 'rejected':
                status_text = "❌ مرفوض"
            else:
                status_text = status
            
            text += f"• {reward_name} - {points_cost}♞ - {status_text} - {date}\n"
    else:
        text += "\nلا توجد عمليات سابقة"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="loyalty_section"))
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        parse_mode="HTML",
        reply_markup=markup
    )

def show_compensation_section(chat_id, message_id):
    """عرض قسم التعويض الخاص"""
    settings = load_compensation_settings()
    
    # التحقق من تفعيل النظام
    if settings.get('compensation_enabled', 'true') != 'true':
        text = """
🛡️ <b>نظام التعويض الخاص</b>

❌ <b>النظام معطل حالياً</b>

سيتم إعلامك عند تفعيل النظام من قبل الإدارة.
"""
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="main_menu"))
        
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode="HTML",
                reply_markup=markup
            )
        except:
            bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=markup
            )
        return
    
    # التحقق من وجود طلب تعويض معلق
    pending_request = db_manager.execute_query(
        "SELECT request_id, created_at FROM compensation_requests WHERE user_id = %s AND status = 'pending'",
        (str(chat_id),)
    )
    
    if pending_request and len(pending_request) > 0:
        request_time = pending_request[0]['created_at'].strftime('%Y-%m-%d %H:%M')
        text = f"""
🛡️ <b>نظام التعويض الخاص</b>

⏳ <b>لديك طلب تعويض معلق</b>

تم تقديم طلب تعويض في: <b>{request_time}</b>
الحالة: <b>في انتظار المراجعة</b>

يرجى الانتظار حتى يتم مراجعة طلبك من قبل الإدارة.
"""
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="main_menu"))
        
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode="HTML",
                reply_markup=markup
            )
        except:
            bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=markup
            )
        return
    
    compensation_rate = float(settings.get('compensation_rate', 0.1)) * 100
    min_loss_amount = float(settings.get('min_loss_amount', 10000))
    
    # حساب الخسارة الإجمالية (بدون استبعاد التعويضات السابقة)
    def get_gross_net_loss(user_id):
        """حساب صافي الخسارة الإجمالية بدون استبعاد التعويضات"""
        try:
            # حساب إجمالي الإيداعات
            deposit_result = db_manager.execute_query(
                "SELECT COALESCE(SUM(amount), 0) as total_deposits FROM transactions "
                "WHERE user_id = %s AND type = 'deposit' AND created_at >= NOW() - INTERVAL '24 hours'",
                (str(user_id),)
            )
            total_deposits = float(deposit_result[0]['total_deposits']) if deposit_result else 0
            
            # حساب إجمالي السحوبات
            withdraw_result = db_manager.execute_query(
                "SELECT COALESCE(SUM(amount), 0) as total_withdrawals FROM transactions "
                "WHERE user_id = %s AND type = 'withdraw' AND created_at >= NOW() - INTERVAL '24 hours'",
                (str(user_id),)
            )
            total_withdrawals = float(withdraw_result[0]['total_withdrawals']) if withdraw_result else 0
            
            # صافي الخسارة الإجمالية = الإيداعات - السحوبات
            gross_net_loss = total_deposits - total_withdrawals
            return max(0, gross_net_loss)
            
        except Exception as e:
            logger.error(f"❌ خطأ في حساب الخسارة الإجمالية: {str(e)}")
            return 0
    
    # حساب الخسارة المتاحة للتعويض (باستبعاد التعويضات السابقة)
    available_net_loss = get_user_net_loss_24h(chat_id)
    gross_net_loss = get_gross_net_loss(chat_id)
    
    # التحقق من الأهلية
    eligible = available_net_loss >= min_loss_amount
    
    # حساب مبلغ التعويض المتوقع
    expected_compensation = available_net_loss * (float(settings.get('compensation_rate', 0.1)))
    
    # الحصول على آخر تعويض
    last_compensation = db_manager.execute_query(
        "SELECT last_compensation_loss, last_compensation_date FROM compensation_tracking "
        "WHERE user_id = %s AND last_compensation_date >= NOW() - INTERVAL '24 hours'",
        (str(chat_id),)
    )
    
    text = f"""
🛡️ <b>نظام التعويض الخاص</b>

<b>الشروط:</b>
• تعويض {compensation_rate}% عند خسارة {min_loss_amount:,.0f} SYP على الأقل
• خلال آخر 24 ساعة

<b>إحصائياتك:</b>
• صافي خسارتك الإجمالية: <b>{gross_net_loss:,.0f} SYP</b>
• الخسارة المتاحة للتعويض: <b>{available_net_loss:,.0f} SYP</b>
• مبلغ التعويض المتوقع: <b>{expected_compensation:,.0f} SYP</b>
"""

    # إضافة معلومات عن آخر تعويض إذا وجد
    if last_compensation and len(last_compensation) > 0:
        last_loss = last_compensation[0]['last_compensation_loss']
        last_date = last_compensation[0]['last_compensation_date'].strftime('%Y-%m-%d %H:%M')
        text += f"""
• آخر تعويض: <b>{last_loss:,.0f} SYP</b> في {last_date}
"""

    text += f"""
• حالتك: {'<b>✅ مؤهل للتعويض</b>' if eligible else '<b>❌ غير مؤهل</b>'}

{'<b>حان موعد الحصول على التعويض، قم بالنقر على الزر ادناه</b>' if eligible else '<b>لم تستوفِ الشروط بعد</b>'}
"""

    # إضافة تلميحات للمستخدم غير المؤهل
    if not eligible:
        if available_net_loss > 0:
            text += f"\n📊 تحتاج إلى خسارة <b>{(min_loss_amount - available_net_loss):,.0f} SYP</b> إضافية للتأهل للتعويض."
        else:
            text += f"\n💡 قم بإجراء عمليات شحن وسحب لتكوين خسارة تصل إلى <b>{min_loss_amount:,.0f} SYP</b> على الأقل."

    markup = types.InlineKeyboardMarkup()
    
    if eligible:
        markup.add(types.InlineKeyboardButton("✅ تقديم طلب تعويض", callback_data="request_compensation"))
    
    markup.add(types.InlineKeyboardButton("🔄 تحديث", callback_data="compensation_section"))
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="main_menu"))
    
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )
    except:
        bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )


def handle_compensation_request(call, chat_id, message_id):
    """معالجة طلب التعويض"""
    settings = load_compensation_settings()
    
    # التحقق من تفعيل النظام
    if settings.get('compensation_enabled', 'true') != 'true':
        bot.answer_callback_query(call.id, text="❌ نظام التعويض معطل حالياً", show_alert=True)
        return
    
    # التحقق من عدم وجود طلب تعويض معلق للمستخدم
    pending_request = db_manager.execute_query(
        "SELECT request_id FROM compensation_requests WHERE user_id = %s AND status = 'pending'",
        (str(chat_id),)
    )
    
    if pending_request and len(pending_request) > 0:
        bot.answer_callback_query(call.id, text="❌ لديك طلب تعويض معلق بالفعل", show_alert=True)
        return
    
    compensation_rate = float(settings.get('compensation_rate', 0.1))
    min_loss_amount = float(settings.get('min_loss_amount', 10000))
    
    # التحقق من الأهلية مرة أخرى
    net_loss = get_user_net_loss_24h(chat_id)
    
    if net_loss < min_loss_amount:
        bot.answer_callback_query(call.id, text="❌ لم تستوفِ شروط التعويض بعد", show_alert=True)
        return
    
    # حساب مبلغ التعويض
    compensation_amount = net_loss * compensation_rate
    
    # ✅ تسجيل الخسارة الحالية كمعوضة (بدون حذف أي بيانات)
    try:
        tracking_success = db_manager.execute_query(
            "INSERT INTO compensation_tracking (user_id, last_compensation_loss, last_compensation_date) "
            "VALUES (%s, %s, CURRENT_TIMESTAMP) "
            "ON CONFLICT (user_id) DO UPDATE SET "
            "last_compensation_loss = EXCLUDED.last_compensation_loss, "
            "last_compensation_date = EXCLUDED.last_compensation_date",
            (str(chat_id), net_loss)
        )
        
        if tracking_success:
            logger.info(f"✅ تم تسجيل خسارة التعويض للمستخدم {chat_id}: {net_loss}")
        else:
            logger.error(f"❌ فشل في تسجيل خسارة التعويض للمستخدم {chat_id}")
            
    except Exception as e:
        logger.error(f"❌ خطأ في تسجيل تتبع التعويض: {str(e)}")
    
    # إنشاء طلب التعويض
    request_text = f"""
🛡️ <b>طلب تعويض جديد</b>

المستخدم: <code>{chat_id}</code>
صافي الخسارة: <b>{net_loss:,.0f} SYP</b>
نسبة التعويض: <b>{compensation_rate * 100}%</b>
مبلغ التعويض: <b>{compensation_amount:,.0f} SYP</b>
الوقت: <b>{time.strftime('%Y-%m-%d %H:%M:%S')}</b>

✅ <i>سيتم إعادة تعيين تقدم التعويض بعد الموافقة</i>
"""
    
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("✅ موافق", callback_data=f"approve_compensation_{chat_id}_{compensation_amount}"),
        types.InlineKeyboardButton("❌ رفض", callback_data=f"reject_compensation_{chat_id}")
    )
    
    group_message_id = send_to_payment_group(request_text, markup)
    group_chat_id = PAYMENT_REQUESTS_CHAT_ID if PAYMENT_REQUESTS_CHAT_ID else ADMIN_CHAT_ID
    
    # إنشاء طلب التعويض
    request_id = add_compensation_request(
        user_id=chat_id, 
        amount=compensation_amount, 
        net_loss=net_loss, 
        message_id=group_message_id, 
        group_chat_id=group_chat_id
    )
    
    if request_id:
        bot.answer_callback_query(call.id, text="✅ تم إرسال طلب التعويض بنجاح")
        
        # تحديث قسم التعويض
        show_compensation_section(chat_id, message_id)
    else:
        bot.answer_callback_query(call.id, text="❌ فشل في إرسال الطلب", show_alert=True)

def handle_approve_compensation(call, chat_id, message_id):
    """معالجة الموافقة على طلب التعويض"""
    if not is_admin(str(call.from_user.id)):
        bot.answer_callback_query(call.id, text="❌ ليس لديك صلاحية الموافقة", show_alert=True)
        return
    
    try:
        parts = call.data.split('_')
        if len(parts) >= 4:
            user_id = parts[2]
            amount = float(parts[3])
            
            # ✅ التصحيح: استخدام استعلام أبسط بدون ORDER BY
            success = db_manager.execute_query(
                "UPDATE compensation_requests SET status = 'approved', approved_at = CURRENT_TIMESTAMP "
                "WHERE user_id = %s AND status = 'pending'",
                (user_id,)
            )
            
            if success:
                # تحديث رصيد المحفظة
                current_balance = get_wallet_balance(user_id)
                new_balance = update_wallet_balance(user_id, amount)
                
                # إرسال إشعار للمستخدم
                try:
                    bot.send_message(
                        user_id,
                        f"""🛡️ <b>تمت الموافقة على طلب التعويض</b>

المبلغ المعوض: <b>{amount:,.0f} SYP</b>
رصيدك الحالي: <b>{new_balance:,.0f} SYP</b>

تمت الإضافة إلى محفظتك بنجاح ✅""",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"خطأ في إرسال إشعار للمستخدم: {str(e)}")
                
                # تحديث رسالة المجموعة
                success_text = f"""
✅ <b>تمت الموافقة على طلب التعويض</b>

المستخدم: <code>{user_id}</code>
المبلغ المعوض: <b>{amount:,.0f} SYP</b>
الرصيد السابق: <b>{current_balance:,.0f} SYP</b>
الرصيد الجديد: <b>{new_balance:,.0f} SYP</b>
وقت التنفيذ: <b>{time.strftime('%Y-%m-%d %H:%M:%S')}</b>

الحالة: مكتمل ✅
"""
                
                # إزالة الأزرار من الرسالة
                edit_group_message(
                    call.message.chat.id,
                    call.message.message_id,
                    success_text,
                    reply_markup=None
                )
                
                bot.answer_callback_query(call.id, text="✅ تمت الموافقة على التعويض")
            else:
                bot.answer_callback_query(call.id, text="❌ فشل في تحديث حالة الطلب", show_alert=True)
        else:
            bot.answer_callback_query(call.id, text="❌ بيانات غير صحيحة", show_alert=True)
            
    except Exception as e:
        logger.error(f"خطأ في معالجة الموافقة على التعويض: {str(e)}")
        bot.answer_callback_query(call.id, text="❌ حدث خطأ في المعالجة", show_alert=True)

def handle_reject_compensation(call, chat_id, message_id):
    """معالجة رفض طلب التعويض"""
    if not is_admin(str(call.from_user.id)):
        bot.answer_callback_query(call.id, text="❌ ليس لديك صلاحية الرفض", show_alert=True)
        return
    
    try:
        parts = call.data.split('_')
        if len(parts) >= 3:
            user_id = parts[2]
            
            # ✅ التصحيح: استخدام استعلام أبسط بدون ORDER BY
            success = db_manager.execute_query(
                "UPDATE compensation_requests SET status = 'rejected', rejected_at = CURRENT_TIMESTAMP "
                "WHERE user_id = %s AND status = 'pending'",
                (user_id,)
            )
            
            if success:
                # إرسال إشعار للمستخدم
                try:
                    bot.send_message(
                        user_id,
                        "❌ <b>تم رفض طلب التعويض</b>\n\nللمزيد من المعلومات يرجى التواصل مع الإدارة.",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"خطأ في إرسال إشعار للمستخدم: {str(e)}")
                
                # تحديث رسالة المجموعة
                rejected_text = f"""
❌ <b>تم رفض طلب التعويض</b>

المستخدم: <code>{user_id}</code>
وقت الرفض: <b>{time.strftime('%Y-%m-%d %H:%M:%S')}</b>

الحالة: مرفوض ❌
"""
                
                # إزالة الأزرار من الرسالة
                edit_group_message(
                    call.message.chat.id,
                    call.message.message_id,
                    rejected_text,
                    reply_markup=None
                )
                
                bot.answer_callback_query(call.id, text="✅ تم رفض الطلب")
            else:
                bot.answer_callback_query(call.id, text="❌ فشل في تحديث حالة الطلب", show_alert=True)
        else:
            bot.answer_callback_query(call.id, text="❌ بيانات غير صحيحة", show_alert=True)
            
    except Exception as e:
        logger.error(f"خطأ في معالجة الرفض: {str(e)}")
        bot.answer_callback_query(call.id, text="❌ حدث خطأ في المعالجة", show_alert=True)





def show_compensation_admin_panel(chat_id, message_id):
    """عرض لوحة إدارة التعويض"""
    settings = load_compensation_settings()
    compensation_rate = float(settings.get('compensation_rate', 0.1)) * 100
    min_loss_amount = float(settings.get('min_loss_amount', 10000))
    enabled = settings.get('compensation_enabled', 'true') == 'true'
    
    # إحصائيات الطلبات
    pending_requests = db_manager.execute_query(
        "SELECT COUNT(*) as count FROM compensation_requests WHERE status = 'pending'"
    )
    pending_count = pending_requests[0]['count'] if pending_requests else 0
    
    approved_requests = db_manager.execute_query(
        "SELECT COUNT(*) as count FROM compensation_requests WHERE status = 'approved'"
    )
    approved_count = approved_requests[0]['count'] if approved_requests else 0
    
    text = f"""
🛡️ <b>إدارة نظام التعويض</b>

<b>الإحصائيات:</b>
• الطلبات المعلقة: <b>{pending_count}</b>
• الطلبات المنجزة: <b>{approved_count}</b>

<b>الإعدادات الحالية:</b>
• نسبة التعويض: <b>{compensation_rate}%</b>
• الحد الأدنى للخسارة: <b>{min_loss_amount:,.0f} SYP</b>
• حالة النظام: <b>{'✅ مفعل' if enabled else '❌ معطل'}</b>

<b>اختر الإجراء المطلوب:</b>
"""
    
    markup = types.InlineKeyboardMarkup()
    
    markup.row(
        types.InlineKeyboardButton("📊 تعديل النسبة", callback_data="edit_compensation_rate"),
        types.InlineKeyboardButton("💰 تعديل المبلغ", callback_data="edit_min_loss_amount")
    )
    
    markup.row(
        types.InlineKeyboardButton(f"{'❌ تعطيل' if enabled else '✅ تفعيل'} النظام", callback_data="toggle_compensation"),
        types.InlineKeyboardButton("📋 الطلبات المعلقة", callback_data="pending_compensations")
    )
    
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel"))
    
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )
    except:
        bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )

def toggle_compensation_system(chat_id, message_id):
    """تبديل حالة نظام التعويض"""
    settings = load_compensation_settings()
    current_status = settings.get('compensation_enabled', 'true')
    new_status = 'false' if current_status == 'true' else 'true'
    
    settings['compensation_enabled'] = new_status
    save_compensation_settings(settings)
    
    status_text = "مفعل ✅" if new_status == 'true' else "معطل ❌"
    bot.answer_callback_query(chat_id, text=f"تم {status_text} نظام التعويض")
    show_compensation_admin_panel(chat_id, message_id)

def show_pending_compensations(chat_id, message_id):
    """عرض طلبات التعويض المعلقة"""
    result = db_manager.execute_query(
        "SELECT * FROM compensation_requests WHERE status = 'pending' ORDER BY created_at DESC"
    )
    
    text = "<b>🛡️ طلبات التعويض المعلقة</b>\n\n"
    
    if result:
        for i, req in enumerate(result, 1):
            text += f"""
{i}. <b>المستخدم:</b> <code>{req['user_id']}</code>
   <b>صافي الخسارة:</b> {float(req['net_loss']):,.0f} SYP
   <b>مبلغ التعويض:</b> {float(req['amount']):,.0f} SYP
   <b>الوقت:</b> {req['created_at'].strftime('%Y-%m-%d %H:%M')}
"""
            
            # أزرار الموافقة والرفض لكل طلب
            # (سنضيف هذا لاحقاً إذا أردت)
    else:
        text += "❌ لا توجد طلبات تعويض معلقة"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="compensation_admin"))
    
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )
    except:
        bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )


# ===============================================================
# دوال إدارة طرق الدفع
# ===============================================================

def show_manage_payment_methods(chat_id, message_id):
    methods = payment_system.get_active_methods()
    
    text = "<b>🛠 إدارة طرق الدفع</b>\n\n"
    if methods:
        for method_id, method in methods.items():
            text += f"• {method['name']}\n"
            text += f"  العنوان: {method['address'][:20]}...\n"
            text += f"  الحد الأدنى: {method['min_amount']}\n"
            text += f"  سعر الصرف: {method['exchange_rate']}\n\n"
    else:
        text += "لا توجد طرق دفع مضافة.\n\n"
    
    text += "اختر الإجراء المطلوب:"
    
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("➕ إضافة طريقة", callback_data="add_payment_method"),
        types.InlineKeyboardButton("📋 عرض الكل", callback_data="manage_payment_methods")
    )
    
    if methods:
        for method_id, method in methods.items():
            markup.row(
                types.InlineKeyboardButton(f"✏️ {method['name']}", callback_data=f"edit_payment_method_{method_id}"),
                types.InlineKeyboardButton(f"🗑️ حذف", callback_data=f"delete_payment_method_{method_id}")
            )
    
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel"))
    
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )
    except:
        bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )

def start_add_payment_method(chat_id):
    user_data[chat_id] = {'state': 'add_payment_name'}
    bot.send_message(
        chat_id,
        "<b>➕ إضافة طريقة دفع جديدة</b>\n\n"
        "الخطوة 1/4: أرسل اسم طريقة الدفع\n"
        "<em>مثال: Bitcoins, USDT, PayPal</em>",
        parse_mode="HTML",
        reply_markup=EnhancedKeyboard.create_back_button("manage_payment_methods")
    )

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'add_payment_name')
def handle_payment_name(message):
    chat_id = str(message.chat.id)
    name = message.text.strip()
    
    if len(name) < 2:
        bot.send_message(chat_id, "❌ اسم الطريقة يجب أن يكون حرفين على الأقل")
        return
    
    user_data[chat_id]['payment_name'] = name
    user_data[chat_id]['state'] = 'add_payment_address'
    
    bot.send_message(
        chat_id,
        "الخطوة 2/4: أرسل عنوان الدفع\n"
        "<em>مثال: 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa</em>",
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'add_payment_address')
def handle_payment_address(message):
    chat_id = str(message.chat.id)
    address = message.text.strip()
    
    if len(address) < 5:
        bot.send_message(chat_id, "❌ العنوان يجب أن يكون 5 أحرف على الأقل")
        return
    
    user_data[chat_id]['payment_address'] = address
    user_data[chat_id]['state'] = 'add_payment_min_amount'
    
    bot.send_message(
        chat_id,
        "الخطوة 3/4: أرسل الحد الأدنى للدفع\n"
        "<em>مثال: 10</em>",
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'add_payment_min_amount')
def handle_payment_min_amount(message):
    chat_id = str(message.chat.id)
    
    try:
        min_amount = float(message.text.strip())
        
        if min_amount <= 0:
            bot.send_message(chat_id, "❌ الحد الأدنى يجب أن يكون أكبر من الصفر")
            return
        
        user_data[chat_id]['payment_min_amount'] = min_amount
        user_data[chat_id]['state'] = 'add_payment_exchange_rate'
        
        bot.send_message(
            chat_id,
            "الخطوة 4/4: أرسل سعر الصرف\n"
            "<em>مثال: 1.0 (للسعر العادي)</em>",
            parse_mode="HTML"
        )
    except ValueError:
        bot.send_message(chat_id, "❌ يرجى إدخال رقم صحيح")

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'add_payment_exchange_rate')
def handle_payment_exchange_rate(message):
    chat_id = str(message.chat.id)
    
    try:
        exchange_rate = float(message.text.strip())
        
        if exchange_rate <= 0:
            bot.send_message(chat_id, "❌ سعر الصرف يجب أن يكون أكبر من الصفر")
            return
        
        # حفظ طريقة الدفع
        name = user_data[chat_id]['payment_name']
        address = user_data[chat_id]['payment_address']
        min_amount = user_data[chat_id]['payment_min_amount']
        
        method_id, message_text = payment_system.add_payment_method(name, address, min_amount, exchange_rate)
        
        bot.send_message(chat_id, message_text, parse_mode="HTML")
        
        # تنظيف البيانات
        if chat_id in user_data:
            del user_data[chat_id]
            
        # العودة إلى القائمة
        show_manage_payment_methods(chat_id, None)
        
    except ValueError:
        bot.send_message(chat_id, "❌ يرجى إدخال رقم صحيح")

def start_edit_payment_method(chat_id, method_id):
    # تنفيذ منطق التعديل هنا
    bot.send_message(chat_id, f"🔧 جاري تطوير خاصية تعديل طريقة الدفع {method_id}")

def confirm_delete_payment_method(chat_id, message_id, method_id):
    method = payment_system.methods.get(method_id)
    if not method:
        bot.answer_callback_query(message_id, text="❌ طريقة الدفع غير موجودة")
        return
    
    text = f"⚠️ <b>تأكيد الحذف</b>\n\nهل أنت متأكد من حذف طريقة الدفع <b>{method['name']}</b>؟"
    
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("✅ نعم", callback_data=f"confirm_delete_payment_{method_id}"),
        types.InlineKeyboardButton("❌ لا", callback_data="manage_payment_methods")
    )
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        parse_mode="HTML",
        reply_markup=markup
    )

# ===============================================================
# دوال إدارة طرق السحب
# ===============================================================

def show_manage_withdraw_methods(chat_id, message_id):
    methods = withdraw_system.get_active_methods()
    
    text = "<b>🛠 إدارة طرق السحب</b>\n\n"
    if methods:
        for method_id, method in methods.items():
            text += f"• {method['name']}\n"
            text += f"  عمولة: {method['commission_rate']*100}%\n\n"
    else:
        text += "لا توجد طرق سحب مضافة.\n\n"
    
    text += "اختر الإجراء المطلوب:"
    
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("➕ إضافة طريقة", callback_data="add_withdraw_method"),
        types.InlineKeyboardButton("📋 عرض الكل", callback_data="manage_withdraw_methods")
    )
    
    if methods:
        for method_id, method in methods.items():
            markup.row(
                types.InlineKeyboardButton(f"✏️ {method['name']}", callback_data=f"edit_withdraw_method_{method_id}"),
                types.InlineKeyboardButton(f"🗑️ حذف", callback_data=f"delete_withdraw_method_{method_id}")
            )
    
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel"))
    
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )
    except:
        bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )

def start_add_withdraw_method(chat_id):
    user_data[chat_id] = {'state': 'add_withdraw_name'}
    bot.send_message(
        chat_id,
        "<b>➕ إضافة طريقة سحب جديدة</b>\n\n"
        "الخطوة 1/2: أرسل اسم طريقة السحب\n"
        "<em>مثال: Binance, Bank Transfer, PayPal</em>",
        parse_mode="HTML",
        reply_markup=EnhancedKeyboard.create_back_button("manage_withdraw_methods")
    )

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'add_withdraw_name')
def handle_withdraw_name(message):
    chat_id = str(message.chat.id)
    name = message.text.strip()
    
    if len(name) < 2:
        bot.send_message(chat_id, "❌ اسم الطريقة يجب أن يكون حرفين على الأقل")
        return
    
    user_data[chat_id]['withdraw_name'] = name
    user_data[chat_id]['state'] = 'add_withdraw_commission'
    
    bot.send_message(
        chat_id,
        "الخطوة 2/2: أرسل نسبة العمولة (بدون %)\n"
        "<em>مثال: 5 (لعمولة 5%)</em>",
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'add_withdraw_commission')
def handle_withdraw_commission(message):
    chat_id = str(message.chat.id)
    
    try:
        commission_percent = float(message.text.strip())
        
        if commission_percent < 0 or commission_percent > 100:
            bot.send_message(chat_id, "❌ نسبة العمولة يجب أن تكون بين 0 و 100")
            return
        
        commission_rate = commission_percent / 100
        
        # حفظ طريقة السحب
        name = user_data[chat_id]['withdraw_name']
        method_id, message_text = withdraw_system.add_withdraw_method(name, commission_rate)
        
        bot.send_message(chat_id, message_text, parse_mode="HTML")
        
        # تنظيف البيانات
        if chat_id in user_data:
            del user_data[chat_id]
            
        # العودة إلى القائمة
        show_manage_withdraw_methods(chat_id, None)
        
    except ValueError:
        bot.send_message(chat_id, "❌ يرجى إدخال رقم صحيح")

def start_edit_withdraw_method(chat_id, method_id):
    # تنفيذ منطق التعديل هنا
    bot.send_message(chat_id, f"🔧 جاري تطوير خاصية تعديل طريقة السحب {method_id}")

def confirm_delete_withdraw_method(chat_id, message_id, method_id):
    method = withdraw_system.methods.get(method_id)
    if not method:
        bot.answer_callback_query(message_id, text="❌ طريقة السحب غير موجودة")
        return
    
    text = f"⚠️ <b>تأكيد الحذف</b>\n\nهل أنت متأكد من حذف طريقة السحب <b>{method['name']}</b>؟"
    
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("✅ نعم", callback_data=f"confirm_delete_withdraw_{method_id}"),
        types.InlineKeyboardButton("❌ لا", callback_data="manage_withdraw_methods")
    )
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        parse_mode="HTML",
        reply_markup=markup
    )






# ===============================================================
# دوال الإدارة المتبقية
# ===============================================================

def show_admin_panel(chat_id, message_id):
    admin_text = """
<b>👨‍💼 لوحة الإدارة</b>

اختر من الخيارات:
    """
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=admin_text,
        parse_mode="HTML",
        reply_markup=EnhancedKeyboard.create_admin_panel()
    )

def show_referral_stats(chat_id, message_id):
    """عرض إحصائيات الإحالات"""
    pending_commissions = get_pending_commissions()
    total_pending = sum(commission['total_pending'] for commission in pending_commissions)
    
    text = f"""
<b>📈 إحصائيات الإحالات</b>

📊 <b>المستحقات المعلقة:</b>
• إجمالي المستحقات: <b>{total_pending:.2f}</b>
• عدد المحيلين: <b>{len(pending_commissions)}</b>

👥 <b>تفاصيل المحيلين:</b>
"""
    
    for commission in pending_commissions:
        text += f"• المستخدم {commission['referrer_id']}: {commission['total_pending']:.2f}\n"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="referral_admin"))
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        parse_mode="HTML",
        reply_markup=markup
    )

def start_edit_commission_rate(chat_id):
    """بدء تعديل نسبة العمولة"""
    user_data[chat_id] = {'state': 'edit_commission_rate'}
    bot.send_message(
        chat_id,
        "<b>📊 تعديل نسبة العمولة</b>\n\n"
        "أرسل النسبة الجديدة (بدون %)\n"
        "<em>مثال: 10 (لنسبة 10%)</em>",
        parse_mode="HTML",
        reply_markup=EnhancedKeyboard.create_back_button("referral_settings")
    )

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'edit_commission_rate')
def handle_edit_commission_rate(message):
    chat_id = str(message.chat.id)
    
    try:
        commission_percent = float(message.text.strip())
        
        if commission_percent < 0 or commission_percent > 100:
            bot.send_message(chat_id, "❌ نسبة العمولة يجب أن تكون بين 0 و 100")
            return
        
        commission_rate = commission_percent / 100
        
        # حفظ الإعدادات
        settings = load_referral_settings()
        settings['commission_rate'] = str(commission_rate)
        save_referral_settings(settings)
        
        bot.send_message(
            chat_id,
            f"✅ تم تحديث نسبة العمولة إلى <b>{commission_percent}%</b>",
            parse_mode="HTML"
        )
        
        # تنظيف البيانات
        if chat_id in user_data:
            del user_data[chat_id]
            
        # العودة إلى الإعدادات
        show_referral_settings(chat_id, None)
        
    except ValueError:
        bot.send_message(chat_id, "❌ يرجى إدخال رقم صحيح")

def start_edit_payout_days(chat_id):
    """بدء تعديل أيام التوزيع"""
    user_data[chat_id] = {'state': 'edit_payout_days'}
    bot.send_message(
        chat_id,
        "<b>⏰ تعديل أيام التوزيع</b>\n\n"
        "أرسل عدد الأيام الجديد\n"
        "<em>مثال: 7 (لتوزيع أسبوعي)</em>",
        parse_mode="HTML",
        reply_markup=EnhancedKeyboard.create_back_button("referral_settings")
    )

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'edit_payout_days')
def handle_edit_payout_days(message):
    chat_id = str(message.chat.id)
    
    try:
        payout_days = int(message.text.strip())
        
        if payout_days < 1:
            bot.send_message(chat_id, "❌ عدد الأيام يجب أن يكون 1 على الأقل")
            return
        
        # حفظ الإعدادات
        settings = load_referral_settings()
        settings['payout_days'] = str(payout_days)
        save_referral_settings(settings)
        
        bot.send_message(
            chat_id,
            f"✅ تم تحديث أيام التوزيع إلى <b>{payout_days}</b> يوم",
            parse_mode="HTML"
        )
        
        # تنظيف البيانات
        if chat_id in user_data:
            del user_data[chat_id]
            
        # العودة إلى الإعدادات
        show_referral_settings(chat_id, None)
        
    except ValueError:
        bot.send_message(chat_id, "❌ يرجى إدخال رقم صحيح")

def handle_approve_payment(call, chat_id, message_id):
    """معالجة الموافقة على طلب الدفع """
    # ✅ التصحيح: التحقق من هوية المستخدم الذي ضغط الزر وليس الدردشة
    if not is_admin(str(call.from_user.id)):
        bot.answer_callback_query(call.id, text="❌ ليس لديك صلاحية الموافقة", show_alert=True)
        return
    
    try:
        # استخراج البيانات من callback_data
        parts = call.data.split('_')
        if len(parts) >= 4:
            user_id = parts[2]
            amount = float(parts[3])
            transaction_id = '_'.join(parts[4:]) if len(parts) > 4 else "غير معروف"
            
            # التحقق من عدم معالجة الطلب مسبقاً
            if is_payment_request_processed(user_id, transaction_id):
                bot.answer_callback_query(call.id, "❌ تم معالجة هذا الطلب مسبقاً", show_alert=True)
                return
            
            # تحديث رصيد المحفظة
            current_balance = get_wallet_balance(user_id)
            new_balance = update_wallet_balance(user_id, amount)
            
            logger.info(f"💰 تحديث الرصيد: المستخدم {user_id}, المبلغ {amount}, الرصيد الجديد {new_balance}")
            
            # تحديث حالة الطلب في قاعدة البيانات
            db_manager.execute_query(
                "UPDATE payment_requests SET status = 'approved', approved_at = CURRENT_TIMESTAMP WHERE user_id = %s AND transaction_id = %s AND status = 'pending'",
                (user_id, transaction_id)
            )
            
            # إرسال إشعار للمستخدم
            try:
                bot.send_message(
                    user_id,
                    f"""✅ <b>تمت الموافقة على طلب الدفع</b>

💰 <b>المبلغ المضاف:</b> {amount}
💳 <b>رصيدك الحالي:</b> {new_balance}

📝 <b>رقم العملية:</b> <code>{transaction_id}</code>""",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"❌ خطأ في إرسال إشعار للمستخدم: {e}")
            
            # تحديث رسالة المجموعة
            success_text = f"""
✅ <b>تمت الموافقة على طلب الدفع</b>

👤 <b>المستخدم:</b> <code>{user_id}</code>
💰 <b>المبلغ:</b> {amount}
💳 <b>الرصيد السابق:</b> {current_balance}
💳 <b>الرصيد الجديد:</b> {new_balance}
⏰ <b>وقت الموافقة:</b> {time.strftime('%Y-%m-%d %H:%M:%S')}
🔢 <b>رقم العملية:</b> <code>{transaction_id}</code>

🟢 <b>الحالة:</b> مكتمل
            """
            
            # إزالة الأزرار من الرسالة
            edit_group_message(
                call.message.chat.id,
                call.message.message_id,
                success_text,
                reply_markup=None
            )
            
            bot.answer_callback_query(call.id, "✅ تمت الموافقة على الطلب")
            
        else:
            bot.answer_callback_query(call.id, "❌ بيانات غير صحيحة", show_alert=True)
            
    except Exception as e:
        logger.error(f"❌ خطأ في معالجة الموافقة: {e}")
        bot.answer_callback_query(call.id, "❌ حدث خطأ في المعالجة", show_alert=True)


def handle_reject_payment(call, chat_id, message_id):
    """معالجة رفض طلب الدفع"""
    # ✅ التصحيح: التحقق من هوية المستخدم الذي ضغط الزر
    if not is_admin(str(call.from_user.id)):
        bot.answer_callback_query(call.id, text="❌ ليس لديك صلاحية الرفض", show_alert=True)
        return
    
    try:
        # استخراج البيانات من callback_data
        parts = call.data.split('_')
        if len(parts) >= 3:
            user_id = parts[2]
            transaction_id = '_'.join(parts[3:]) if len(parts) > 3 else parts[3] if len(parts) > 3 else "غير معروف"
            
            # تحديث حالة الطلب في قاعدة البيانات
            db_manager.execute_query(
                "UPDATE payment_requests SET status = 'rejected', rejected_at = CURRENT_TIMESTAMP WHERE user_id = %s AND transaction_id = %s AND status = 'pending'",
                (user_id, transaction_id)
            )
            
            # إرسال إشعار للمستخدم
            try:
                bot.send_message(
                    user_id,
                    f"""❌ <b>تم رفض طلب الدفع</b>

📝 <b>رقم العملية:</b> <code>{transaction_id}</code>

💡 <i>للمزيد من المعلومات، يرجى التواصل مع الدعم</i>""",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"❌ خطأ في إرسال إشعار للمستخدم: {e}")
            
            # تحديث رسالة المجموعة
            rejected_text = f"""
❌ <b>تم رفض طلب الدفع</b>

👤 <b>المستخدم:</b> <code>{user_id}</code>
⏰ <b>وقت الرفض:</b> {time.strftime('%Y-%m-%d %H:%M:%S')}
🔢 <b>رقم العملية:</b> <code>{transaction_id}</code>

🔴 <b>الحالة:</b> مرفوض
            """
            
            # إزالة الأزرار من الرسالة
            edit_group_message(
                call.message.chat.id,
                call.message.message_id,
                rejected_text,
                reply_markup=None
            )
            
            bot.answer_callback_query(call.id, "❌ تم رفض الطلب")
            
        else:
            bot.answer_callback_query(call.id, "❌ بيانات غير صحيحة", show_alert=True)
            
    except Exception as e:
        logger.error(f"❌ خطأ في معالجة الرفض: {e}")
        bot.answer_callback_query(call.id, "❌ حدث خطأ في المعالجة", show_alert=True)

def handle_complete_withdrawal(call, chat_id, message_id):
    """معالجة اكتمال طلب السحب"""
    # ✅ التصحيح: التحقق من هوية المستخدم الذي ضغط الزر
    if not is_admin(str(call.from_user.id)):
        bot.answer_callback_query(call.id, text="❌ ليس لديك صلاحية التنفيذ", show_alert=True)
        return
    
    try:
        # استخراج البيانات من callback_data
        parts = call.data.split('_')
        if len(parts) >= 3:
            withdrawal_id = parts[2]
            
            # تحديث حالة السحب في قاعدة البيانات
            success = db_manager.execute_query(
                "UPDATE pending_withdrawals SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE withdrawal_id = %s AND status = 'pending'",
                (withdrawal_id,)
            )
            
            if success:
                # جلب بيانات السحب
                result = db_manager.execute_query(
                    "SELECT user_id, amount FROM pending_withdrawals WHERE withdrawal_id = %s",
                    (withdrawal_id,)
                )
                
                if result and len(result) > 0:
                    user_id = result[0]['user_id']
                    amount = float(result[0]['amount'])
                    
                    # إرسال إشعار للمستخدم
                    try:
                        bot.send_message(
                            user_id,
                            f"""✅ <b>تم تنفيذ طلب السحب بنجاح</b>

💸 <b>المبلغ المسحوب:</b> {amount}

⏰ <b>وقت التنفيذ:</b> {time.strftime('%Y-%m-%d %H:%M:%S')}""",
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.error(f"❌ خطأ في إرسال إشعار للمستخدم: {e}")
                
                # تحديث رسالة المجموعة
                completed_text = f"""
✅ <b>تم تنفيذ طلب السحب بنجاح</b>

🆔 <b>رقم الطلب:</b> <code>{withdrawal_id}</code>
⏰ <b>وقت التنفيذ:</b> {time.strftime('%Y-%m-%d %H:%M:%S')}

🟢 <b>الحالة:</b> مكتمل
                """
                
                # إزالة الأزرار من الرسالة
                edit_group_message(
                    call.message.chat.id,
                    call.message.message_id,
                    completed_text,
                    reply_markup=None
                )
                
                bot.answer_callback_query(call.id, "✅ تم تنفيذ السحب")
            else:
                bot.answer_callback_query(call.id, "❌ الطلب غير موجود أو تم معالجته مسبقاً", show_alert=True)
                
        else:
            bot.answer_callback_query(call.id, "❌ بيانات غير صحيحة", show_alert=True)
            
    except Exception as e:
        logger.error(f"❌ خطأ في معالجة السحب: {e}")
        bot.answer_callback_query(call.id, "❌ حدث خطأ في المعالجة", show_alert=True)

def show_loyalty_admin_panel(chat_id, message_id):
    """عرض لوحة إدارة نظام النقاط"""
    settings = load_loyalty_settings()
    pending_requests = get_pending_redemptions()
    top_users = get_top_users_by_points(5)
    
    text = f"""
<b>⚙️ إدارة نظام نقاط الامتياز</b>

📊 <b>الإحصائيات:</b>
• طلبات الاستبدال المعلقة: {len(pending_requests)}
• نظام الاستبدال: {'✅ مفعل' if settings.get('redemption_enabled') == 'true' else '❌ معطل'}

⚙️ <b>الإعدادات الحالية:</b>
• النقاط لكل 10,000: {settings.get('points_per_10000', 1)}♞
• نقاط الإحالة: {settings.get('referral_points', 1)}♞
• مكافأة الإيداع الأولى: {settings.get('first_deposit_bonus', 3)}♞
• الحد الأدنى للاستبدال: {settings.get('min_redemption_points', 100)}♞
• أيام التصفير: {settings.get('reset_days', 30)} يوم

🏆 <b>أفضل 5 مستخدمين:</b>
"""
    
    if top_users:
        for i, user in enumerate(top_users, 1):
            text += f"{i}. {user['user_id'][:8]}... - {user['points']}♞\n"
    else:
        text += "لا توجد بيانات\n"
    
    text += "\nاختر الإجراء المطلوب:"
    
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("⚙️ الإعدادات", callback_data="loyalty_settings"),
        types.InlineKeyboardButton("📋 الطلبات", callback_data="loyalty_requests")
    )
    markup.row(
        types.InlineKeyboardButton("🔄 تفعيل/تعطيل", callback_data="loyalty_toggle"),
        types.InlineKeyboardButton("📊 الإحصائيات", callback_data="loyalty_stats")
    )
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel"))
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        parse_mode="HTML",
        reply_markup=markup
    )

def show_loyalty_settings_admin(chat_id, message_id):
    """عرض إعدادات نظام النقاط للمشرفين"""
    settings = load_loyalty_settings()
    
    text = f"""
<b>⚙️ إعدادات نظام نقاط الامتياز</b>

الإعدادات الحالية:
• 📊 النقاط لكل 10,000: {settings.get('points_per_10000', 1)}♞
• 👥 نقاط الإحالة: {settings.get('referral_points', 1)}♞
• 🎁 مكافأة الإيداع الأولى: {settings.get('first_deposit_bonus', 3)}♞
• 💰 الحد الأدنى للاستبدال: {settings.get('min_redemption_points', 100)}♞
• 🔄 أيام التصفير: {settings.get('reset_days', 30)} يوم
• 🔰 نظام الاستبدال: {'✅ مفعل' if settings.get('redemption_enabled') == 'true' else '❌ معطل'}

اختر الإعداد لتعديله:
"""
    
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("📊 نقاط كل 10,000", callback_data="edit_points_per_10000"),
        types.InlineKeyboardButton("👥 نقاط الإحالة", callback_data="edit_referral_points")
    )
    markup.row(
        types.InlineKeyboardButton("🎁 مكافأة الإيداع", callback_data="edit_deposit_bonus"),
        types.InlineKeyboardButton("💰 حد الاستبدال", callback_data="edit_min_redemption")
    )
    markup.row(
        types.InlineKeyboardButton("🔄 أيام التصفير", callback_data="edit_reset_days"),
        types.InlineKeyboardButton("🎯 إدارة الجوائز", callback_data="manage_rewards")
    )
    markup.row(
        types.InlineKeyboardButton("🔄 تصفير الكل", callback_data="reset_all_points"),
        types.InlineKeyboardButton("📤 تصدير البيانات", callback_data="export_points_data")
    )
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="loyalty_admin"))
    
    if message_id:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )
    else:
        bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup
        )

def show_pending_redemption_requests(chat_id, message_id):
    """عرض طلبات الاستبدال المعلقة"""
    requests = get_pending_redemptions()
    
    text = f"""
<b>📋 طلبات استبدال النقاط المعلقة</b>

"""
    
    if requests:
        for i, req in enumerate(requests, 1):
            text += f"""
{i}. <b>المستخدم:</b> {req['user_id']}
   <b>الجائزة:</b> {req['reward_name']}
   <b>التكلفة:</b> {req['points_cost']}♞
   <b>الوقت:</b> {req['created_at'].strftime('%Y-%m-%d %H:%M')}
"""
            markup = types.InlineKeyboardMarkup()
            markup.row(
                types.InlineKeyboardButton("✅ قبول", callback_data=f"approve_redemption_{req['redemption_id']}"),
                types.InlineKeyboardButton("❌ رفض", callback_data=f"reject_redemption_{req['redemption_id']}")
            )
    else:
        text += "لا توجد طلبات معلقة"
        markup = types.InlineKeyboardMarkup()
    
    markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="loyalty_admin"))
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        parse_mode="HTML",
        reply_markup=markup
    )


@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and 
                    user_data[str(message.chat.id)].get('state') == 'edit_compensation_rate')
def handle_edit_compensation_rate(message):
    """معالجة تعديل نسبة التعويض"""
    chat_id = str(message.chat.id)
    
    try:
        rate_percent = float(message.text.strip())
        
        if rate_percent < 0 or rate_percent > 100:
            bot.send_message(chat_id, "❌ يجب أن تكون النسبة بين 0 و 100")
            return
        
        rate_decimal = rate_percent / 100
        
        settings = load_compensation_settings()
        settings['compensation_rate'] = str(rate_decimal)
        save_compensation_settings(settings)
        
        bot.send_message(chat_id, f"✅ تم تحديث نسبة التعويض إلى <b>{rate_percent}%</b>", parse_mode="HTML")
        
        # تنظيف البيانات
        if chat_id in user_data:
            del user_data[chat_id]
        
        # العودة للوحة الإدارة
        show_compensation_admin_panel(chat_id, None)
        
    except ValueError:
        bot.send_message(chat_id, "❌ يرجى إدخال رقم صحيح")

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and 
                    user_data[str(message.chat.id)].get('state') == 'edit_min_loss_amount')
def handle_edit_min_loss_amount(message):
    """معالجة تعديل الحد الأدنى للخسارة"""
    chat_id = str(message.chat.id)
    
    try:
        min_amount = float(message.text.strip())
        
        if min_amount < 0:
            bot.send_message(chat_id, "❌ يجب أن يكون المبلغ أكبر من 0")
            return
        
        settings = load_compensation_settings()
        settings['min_loss_amount'] = str(min_amount)
        save_compensation_settings(settings)
        
        bot.send_message(chat_id, f"✅ تم تحديث الحد الأدنى للخسارة إلى <b>{min_amount:,.0f} SYP</b>", parse_mode="HTML")
        
        # تنظيف البيانات
        if chat_id in user_data:
            del user_data[chat_id]
        
        # العودة للوحة الإدارة
        show_compensation_admin_panel(chat_id, None)
        
    except ValueError:
        bot.send_message(chat_id, "❌ يرجى إدخال رقم صحيح")


def start_contact_support(chat_id):
    """بدء التواصل مع الدعم"""
    user_data[chat_id] = {'state': 'awaiting_support_message'}
    
    support_text = """
📞 <b>التواصل مع الدعم</b>

يمكنك التواصل مع الدعم إذا كان لديك أي استفسار أو مشكلة.
نحن متواجدون في أي وقت لمساعدتك.

يرجى إدخال رسالتك أو إرسال الصورة التي تريد إرسالها للدعم:
"""
    
    bot.send_message(
        chat_id,
        support_text,
        parse_mode="HTML",
        reply_markup=EnhancedKeyboard.create_back_button("main_menu")
    )

def handle_support_message(message):
    """معالجة رسالة الدعم من المستخدم"""
    chat_id = str(message.chat.id)
    
    # حفظ البيانات المؤقتة
    if chat_id not in user_data:
        user_data[chat_id] = {}
    
    user_data[chat_id]['support_message'] = message.text
    user_data[chat_id]['state'] = 'support_message_received'
    
    # إرسال تأكيد
    bot.send_message(
        chat_id,
        "📨 <b>تم استلام رسالتك</b>\n\nهل تريد إرسالها للدعم؟",
        parse_mode="HTML",
        reply_markup=EnhancedKeyboard.create_confirmation_buttons(
            "confirm_support_message",
            "cancel_support_message"
        )
    )

def handle_support_photo(message):
    """معالجة صورة الدعم من المستخدم"""
    chat_id = str(message.chat.id)
    
    if chat_id not in user_data:
        user_data[chat_id] = {}
    
    # حفظ معرف الصورة
    user_data[chat_id]['support_photo_id'] = message.photo[-1].file_id
    user_data[chat_id]['state'] = 'support_photo_received'
    
    # إذا كان هناك نص مع الصورة
    if message.caption:
        user_data[chat_id]['support_message'] = message.caption
    
    # طلب تأكيد الإرسال
    bot.send_message(
        chat_id,
        "🖼️ <b>تم استلام صورتك</b>\n\nهل تريد إرسالها للدعم؟",
        parse_mode="HTML",
        reply_markup=EnhancedKeyboard.create_confirmation_buttons(
            "confirm_support_message",
            "cancel_support_message"
        )
    )

def send_support_request_to_admin(chat_id, username, message_text=None, photo_id=None):
    """إرسال طلب الدعم للإدارة"""
    try:
        # نص الرسالة للإدارة
        admin_text = f"""
📞 <b>طلب دعم جديد</b>

👤 <b>المستخدم:</b> <code>{chat_id}</code>
🆔 <b>اسم المستخدم:</b> @{username if username else 'غير متوفر'}
⏰ <b>الوقت:</b> {time.strftime('%Y-%m-%d %H:%M:%S')}

"""
        
        if message_text:
            admin_text += f"📝 <b>الرسالة:</b>\n{message_text}\n\n"
        
        admin_text += "اختر الإجراء المناسب:"
        
        # إنشاء أزرار الرد
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton("💬 الرد على المستخدم", callback_data=f"reply_to_user_{chat_id}"),
            types.InlineKeyboardButton("✅ تمت المعالجة", callback_data=f"close_support_{chat_id}")
        )
        
        # إرسال الرسالة للإدارة
        if photo_id:
            # إرسال صورة مع نص
            sent_message = bot.send_photo(
                ADMIN_CHAT_ID,
                photo_id,
                caption=admin_text,
                parse_mode="HTML",
                reply_markup=markup
            )
        else:
            # إرسال نص فقط
            sent_message = bot.send_message(
                ADMIN_CHAT_ID,
                admin_text,
                parse_mode="HTML",
                reply_markup=markup
            )
        
        # حفظ طلب الدعم في قاعدة البيانات
        request_id = add_support_request(chat_id, username, message_text, photo_id)
        
        if request_id and sent_message:
            update_support_admin_message(request_id, ADMIN_CHAT_ID, sent_message.message_id)
        
        return True
        
    except Exception as e:
        logger.error(f"خطأ في إرسال طلب الدعم للإدارة: {str(e)}")
        return False

def confirm_support_message(call):
    """تأكيد إرسال رسالة الدعم"""
    chat_id = str(call.message.chat.id)
    
    if chat_id not in user_data:
        bot.answer_callback_query(call.id, "❌ لم يتم العثور على البيانات")
        return
    
    # جلب البيانات
    message_text = user_data[chat_id].get('support_message')
    photo_id = user_data[chat_id].get('support_photo_id')
    username = call.from_user.username
    
    # إرسال للإدارة
    success = send_support_request_to_admin(chat_id, username, message_text, photo_id)
    
    if success:
        # إرسال تأكيد للمستخدم
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text="✅ <b>تم إرسال رسالتك للدعم بنجاح</b>\n\nسيتم الرد عليك في أقرب وقت ممكن.",
            parse_mode="HTML"
        )
        
        # تنظيف البيانات
        if chat_id in user_data:
            keys_to_remove = ['support_message', 'support_photo_id', 'state']
            for key in keys_to_remove:
                user_data[chat_id].pop(key, None)
    else:
        bot.answer_callback_query(call.id, "❌ فشل في إرسال الرسالة", show_alert=True)

def cancel_support_message(call):
    """إلغاء إرسال رسالة الدعم"""
    chat_id = str(call.message.chat.id)
    
    # تنظيف البيانات
    if chat_id in user_data:
        keys_to_remove = ['support_message', 'support_photo_id', 'state']
        for key in keys_to_remove:
            user_data[chat_id].pop(key, None)
    
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text="❌ <b>تم إلغاء إرسال الرسالة</b>",
        parse_mode="HTML",
        reply_markup=EnhancedKeyboard.create_back_button("main_menu")
    )
def start_admin_reply(call, user_id):
    """بدء الرد على المستخدم من قبل الإدارة"""
    admin_chat_id = str(call.message.chat.id)
    message_id = call.message.message_id
    
    # حفظ بيانات الرد
    user_data[admin_chat_id] = {
        'state': 'admin_reply_message',
        'reply_user_id': user_id,
        'support_message_id': message_id
    }
    
    bot.send_message(
        admin_chat_id,
        f"💬 <b>الرد على المستخدم</b>\n\nأرسل رسالة الرد للمستخدم <code>{user_id}</code>:",
        parse_mode="HTML",
        reply_markup=EnhancedKeyboard.create_back_button("admin_panel")
    )

def handle_admin_reply(message):
    """معالجة رد الإدارة"""
    admin_chat_id = str(message.chat.id)
    
    if admin_chat_id not in user_data or user_data[admin_chat_id].get('state') != 'admin_reply_message':
        return
    
    user_id = user_data[admin_chat_id].get('reply_user_id')
    reply_text = message.text
    
    try:
        # إرسال الرد للمستخدم
        bot.send_message(
            user_id,
            f"📨 <b>رد من الدعم</b>\n\n{reply_text}",
            parse_mode="HTML"
        )
        
        # تأكيد للإدارة
        bot.send_message(
            admin_chat_id,
            f"✅ <b>تم إرسال الرد للمستخدم</b> <code>{user_id}</code>",
            parse_mode="HTML"
        )
        
        # تحديث رسالة الدعم الأصلية
        support_message_id = user_data[admin_chat_id].get('support_message_id')
        try:
            bot.edit_message_reply_markup(
                chat_id=admin_chat_id,
                message_id=support_message_id,
                reply_markup=None
            )
        except:
            pass
        
        # تنظيف البيانات
        if admin_chat_id in user_data:
            keys_to_remove = ['reply_user_id', 'support_message_id', 'state']
            for key in keys_to_remove:
                user_data[admin_chat_id].pop(key, None)
                
    except Exception as e:
        bot.send_message(
            admin_chat_id,
            f"❌ <b>فشل في إرسال الرد:</b> {str(e)}",
            parse_mode="HTML"
        )

def close_support_request(call, user_id):
    """إغلاق طلب الدعم"""
    admin_chat_id = str(call.message.chat.id)
    message_id = call.message.message_id
    
    try:
        # تحديث الرسالة
        bot.edit_message_reply_markup(
            chat_id=admin_chat_id,
            message_id=message_id,
            reply_markup=None
        )
        
        # إرسال تأكيد
        bot.answer_callback_query(call.id, "✅ تم إغلاق طلب الدعم")
        
    except Exception as e:
        logger.error(f"خطأ في إغلاق طلب الدعم: {str(e)}")
        bot.answer_callback_query(call.id, "❌ فشل في إغلاق الطلب")

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and 
                    user_data[str(message.chat.id)].get('state') == 'awaiting_support_message')
def handle_support_message_input(message):
    """معالجة رسالة الدعم"""
    handle_support_message(message)

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and 
                    user_data[str(message.chat.id)].get('state') == 'admin_reply_message')
def handle_admin_reply_input(message):
    """معالجة رد الإدارة"""
    handle_admin_reply(message)

@bot.message_handler(content_types=['photo'])
def handle_support_photo_input(message):
    """معالجة صورة الدعم"""
    chat_id = str(message.chat.id)
    if chat_id in user_data and user_data[chat_id].get('state') == 'awaiting_support_message':
        handle_support_photo(message)


@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'gift_user_id')
def handle_gift_user_id_input(message):
    handle_gift_user_id(message)

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'gift_amount')
def handle_gift_amount_input(message):
    handle_gift_amount(message)

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'edit_gift_commission')
def handle_edit_gift_commission_input(message):
    handle_edit_gift_commission(message)

@bot.message_handler(func=lambda message: str(message.chat.id) in user_data and user_data[str(message.chat.id)].get('state') == 'edit_gift_min_amount')
def handle_edit_gift_min_amount_input(message):
    handle_edit_gift_min_amount(message)


# ===============================================================
# نظام التذكير التلقائي
# ===============================================================

def start_referral_reminder():
    """تشغيل نظام التذكير بالتوزيع"""
    def reminder_loop():
        while True:
            try:
                if check_payout_time():
                    send_payout_notification()
                time.sleep(3600)  # التحقق كل ساعة
            except Exception as e:
                logger.error(f"❌ خطأ في نظام التذكير: {str(e)}")
                time.sleep(300)
    
    reminder_thread = threading.Thread(target=reminder_loop, daemon=True)
    reminder_thread.start()

# ===============================================================
# تشغيل النظام المحسن
# ===============================================================

def start_system():
    """تشغيل جميع أنظمة البوت"""
    logger.info("🚀 بدء تشغيل النظام...")
    
    # انتظار بسيط لضمان اكتمال إنشاء الجداول
    time.sleep(2)
    
    # بدء معالجة الطابور
    queue_thread = threading.Thread(target=process_account_operations, daemon=True)
    queue_thread.start()
    logger.info("✅ تم بدء معالجة الطابور")
    
    # بدء نظام التذكير بالإحالات
    start_referral_reminder()
    logger.info("✅ تم تشغيل نظام تذكير الإحالات")
    
    logger.info("🔗 جاري تسجيل الدخول...")
    if agent.ensure_login():
        logger.info("✅ تم تسجيل الدخول بنجاح")
    else:
        logger.error("❌ فشل تسجيل الدخول الأولي")
    
    logger.info("🤖 البوت يعمل واستقبال الرسائل...")
    
    while True:
        try:
            bot.polling(none_stop=True, timeout=60, skip_pending=True)
        except Exception as e:
            logger.error(f"❌ خطأ في تشغيل البوت: {e}")
            logger.info("🔄 إعادة المحاولة بعد 10 ثواني...")
            time.sleep(10)

if __name__ == "__main__":
    start_system()





