import requests
import json
import os
import time
from datetime import datetime
import pandas as pd
import sqlite3
from typing import Dict, List, Optional
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
import logging
from configparser import ConfigParser
import sys

# Configuration and Logging Setup
CONFIG_FILE = "config.ini"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading_bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

class SuperTradingBot:
    def __init__(self):
        self.load_config()
        self.setup_headers()
        self.ensure_data_dir()
        self.init_database()
        self.model, self.scaler = None, None
        self.max_retries = 3
        self.retry_delay = 5

    def load_config(self) -> None:
        config = ConfigParser()
        if not os.path.exists(CONFIG_FILE):
            raise FileNotFoundError(f"Configuration file {CONFIG_FILE} not found")
        config.read(CONFIG_FILE)
        self.api_keys = {
            'gmgn': config.get('API_KEYS', 'GMGN_API_KEY'),
            'solscan': config.get('API_KEYS', 'SOLSCAN_API_KEY'),
            'rugcheck': config.get('API_KEYS', 'RUGCHECK_API_KEY'),
            'toxisolbot': config.get('API_KEYS', 'TOXISOLBOT_API_KEY')
        }
        self.base_urls = {
            'gmgn': config.get('ENDPOINTS', 'GMGN_BASE_URL'),
            'solscan': config.get('ENDPOINTS', 'SOLSCAN_BASE_URL'),
            'rugcheck': config.get('ENDPOINTS', 'RUGCHECK_BASE_URL'),
            'toxisolbot': config.get('ENDPOINTS', 'TOXISOLBOT_BASE_URL')
        }
        self.data_dir = config.get('PATHS', 'DATA_DIR', fallback='coin_data')
        self.db_file = config.get('PATHS', 'DB_FILE', fallback='token_data.db')
        self.csv_file = config.get('PATHS', 'CSV_FILE', fallback='valid_tokens.csv')
        self.categories = config.get('SETTINGS', 'CATEGORIES', fallback='rugged,pumped,tier1,cex_listed').split(',')

    def setup_headers(self) -> None:
        self.headers = {
            'gmgn': {"Authorization": f"Bearer {self.api_keys['gmgn']}"},
            'solscan': {"Authorization": f"Bearer {self.api_keys['solscan']}"},
            'rugcheck': {"x-api-key": self.api_keys['rugcheck']},
            'toxisolbot': {"Authorization": f"Bearer {self.api_keys['toxisolbot']}"}
        }

    def ensure_data_dir(self) -> None:
        try:
            os.makedirs(self.data_dir, exist_ok=True)
        except OSError as e:
            logger.error(f"Failed to create data directory: {e}")
            raise

    def init_database(self) -> None:
        try:
            with sqlite3.connect(self.db_file) as conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS tokens (
                        token_address TEXT PRIMARY KEY,
                        name TEXT,
                        category TEXT,
                        top_holders INTEGER,
                        trading_volume REAL,
                        price_trend REAL,
                        status TEXT,
                        risk_score REAL,
                        timestamp TEXT
                    );
                    CREATE TABLE IF NOT EXISTS historical_data (
                        token_address TEXT,
                        timestamp TEXT,
                        price REAL,
                        volume REAL,
                        dev_wallet_activity INTEGER,
                        linked_wallets INTEGER,
                        past_dev_success REAL,
                        FOREIGN KEY (token_address) REFERENCES tokens(token_address)
                    );
                """)
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Database initialization failed: {e}")
            raise

    def api_request(self, service: str, endpoint: str, method: str = 'GET', data: Optional[Dict] = None) -> Optional[Dict]:
        url = f"{self.base_urls[service]}{endpoint}"
        headers = self.headers[service]
        for attempt in range(self.max_retries):
            try:
                if method == 'GET':
                    response = requests.get(url, headers=headers, timeout=10)
                elif method == 'POST':
                    response = requests.post(url, headers=headers, json=data, timeout=10)
                response.raise_for_status()
                return response.json()
            except requests.RequestException as e:
                logger.warning(f"{service} API request failed (attempt {attempt + 1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
                else:
                    logger.error(f"All retries failed for {service} API: {url}")
                    return None

    def fetch_gmgn_data(self, category: str) -> Optional[List[Dict]]:
        data = self.api_request('gmgn', f"/coins/{category}")
        return data.get("data", []) if data else None

    def process_token(self, token_address: str) -> None:
        try:
            rugcheck_data = self.api_request('rugcheck', f"/tokens/{token_address}/report/summary")
            if not rugcheck_data:
                return

            status = rugcheck_data.get("status", "UNKNOWN").upper()
            name = rugcheck_data.get("name", "Unknown")
            risk_score = float(rugcheck_data.get("risk_score", 0.0))

            gmgn_metrics = self.api_request('gmgn', f"/tokens/{token_address}/metrics")
            top_holders = gmgn_metrics.get("top_holders_count", 0) if gmgn_metrics else 0
            trading_volume = gmgn_metrics.get("trading_volume_24h", 0.0) if gmgn_metrics else 0.0
            price_data = gmgn_metrics.get("price_history", []) if gmgn_metrics else []
            price_trend = ((price_data[-1] - price_data[0]) / price_data[0] * 100) if len(price_data) >= 5 else 0.0

            historical_data = self.api_request('solscan', f"/token/{token_address}/history")
            if historical_data:
                self.save_historical_data(token_address, historical_data.get("history", []))

            if status == "GOOD":
                with sqlite3.connect(self.db_file) as conn:
                    conn.execute("""
                        INSERT OR REPLACE INTO tokens 
                        (token_address, name, category, top_holders, trading_volume, price_trend, status, risk_score, timestamp)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (token_address, name, "manual", top_holders, trading_volume, price_trend, status, risk_score, datetime.now().isoformat()))
                    conn.commit()
                
                self.toxisolbot_trade(token_address, "buy", 0.1)
                self.toxisolbot_alert(f"GOOD token found: {name} ({token_address}) - Bought 0.1 SOL worth")
                logger.info(f"Processed GOOD token: {token_address}")
            else:
                logger.info(f"Token {token_address} status: {status}, skipping trade")

        except Exception as e:
            logger.error(f"Error processing token {token_address}: {e}")

    def run(self) -> None:
        logger.info("Super Trading Bot started")
        while True:
            try:
                for category in self.categories:
                    data = self.fetch_gmgn_data(category)
                    if data:
                        self.save_gmgn_data(category, data)

                token_address = input("Enter token CA (or 'exit'): ").strip()
                if token_address.lower() == "exit":
                    break
                if token_address:
                    self.process_token(token_address)

                ranked_tokens = self.predict_and_rank()
                if ranked_tokens:
                    logger.info("Top 5 Tokens by Success Probability:")
                    for token in ranked_tokens[:5]:
                        logger.info(f"{token['name']} ({token['token_address']}): {token['success_probability']:.2%}")
                        if token["success_probability"] > 0.7:
                            self.toxisolbot_trade(token["token_address"], "buy", 0.05)
                            self.toxisolbot_alert(f"High probability token: {token['name']} - Bought 0.05 SOL worth")

                time.sleep(10)

            except Exception as e:
                logger.error(f"Main loop error: {e}")
                time.sleep(60)  # Wait longer after an error

        self.export_to_csv()
        logger.info("Bot stopped")

if __name__ == "__main__":
    try:
        bot = SuperTradingBot()
        bot.run()
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        bot.export_to_csv()
        sys.exit(1)