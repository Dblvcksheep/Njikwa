import asyncio
import requests
from sqlalchemy import create_engine, Column, String, Float, DateTime, Integer, BigInteger, ForeignKey, Text, Date, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, relationship,backref
from datetime import datetime, timedelta
import time
from time import sleep
import hmac
import hashlib
import json
from typing import Final
import os
from Crypto.Cipher import AES
import base64
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv
import threading
load_dotenv()

from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes



Account.enable_unaudited_hdwallet_features()
encryption_key =os.environ['ENCRYPTION_KEY'].encode()
BASE_RPC = os.environ['BASE_RPC']
w3 = Web3(Web3.HTTPProvider(BASE_RPC))



Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True)
    wallet = Column(String)
    p_key = Column(String)
    state = Column(String, default=None)
    subscribed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now())
    account = relationship('Account', backref='user',cascade="all, delete-orphan")

class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    last_day = Column(Date)
    account_id = Column(String)
    broker = Column(String)
    password = Column(String)
    risk_balance = Column(Integer)
    risk = Column(Integer)
    created_at = Column(DateTime)

engine = create_engine(os.environ['DATABASE'], echo=False)

# Create tables
# Base.metadata.drop_all(engine)
Base.metadata.create_all(engine)

# Session factory
Session = sessionmaker(bind=engine)

def encrypt_private_key(private_key):
    cipher = AES.new(encryption_key, AES.MODE_EAX)
    nonce = cipher.nonce
    ciphertext, tag = cipher.encrypt_and_digest(private_key.encode())
    return base64.b64encode(nonce + tag + ciphertext).decode()

def decrypt_private_key(enc_private_key):
    data = base64.b64decode(enc_private_key)
    nonce, tag, ciphertext = data[:16], data[16:32], data[32:]
    cipher = AES.new(encryption_key, AES.MODE_EAX, nonce=nonce)
    return cipher.decrypt_and_verify(ciphertext, tag).decode()

def create_wallet_for_new_user():
    # Generate wallet
    acct, mnemonic= Account.create_with_mnemonic()

    return {
        'wallet':acct.address,
        'p_key':encrypt_private_key(acct.key.hex())
    }


def connect_wallet(user):

    session = Session()

    # Ensure user exists
    user = session.query(User).filter_by(telegram_id=user).first()

    private_key = decrypt_private_key(user.p_key)
    acct = Account.from_key(private_key)
    return acct


def check_eth_balance(acct, amount):
    """
    acct: Account object from eth_account (Account.from_key)
    amount: float or int (e.g. 0.05 for 0.05 ETH)
    """

    wallet_address = acct.address

    # Get balance in wei
    balance_wei = w3.eth.get_balance(wallet_address)

    # Convert human amount to wei
    required_wei = Web3.to_wei(amount, "ether")

    # Compare
    has_funds = balance_wei >= required_wei

    return {
        "wallet": wallet_address,
        "balance_raw": balance_wei,
        "balance_eth": float(Web3.from_wei(balance_wei, "ether")),
        "required_eth": amount,
        "has_enough": has_funds,
    }


def send_eth(user_acct, amount, receiver):
    """
    Send Base ETH from a user's custodial wallet without specifying gas.

    Args:
        user_acct: Local account object with private key (custodial)
        amount: float, ETH amount to send
        receiver: str, recipient address

    Returns:
        dict with success, tx_hash, receipt or error
    """
    receiver = Web3.to_checksum_address(receiver)
    try:
        web3 = w3  # your Web3 instance connected to Base

        # Convert ETH to Wei
        amount_wei = int(amount * 10 ** 18)

        # Build transaction (gas/gasPrice omitted)
        tx = {
            "from": user_acct.address,
            "to": receiver,
            "value": amount_wei,
            "nonce": web3.eth.get_transaction_count(user_acct.address),
            "gas": 21000,
            "gasPrice": w3.eth.gas_price,
            "chainId": web3.eth.chain_id,
        }

        # Sign the transaction
        signed_tx = user_acct.sign_transaction(tx)

        # Send raw transaction
        tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)

        # Wait for receipt
        receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

        if receipt.status == 1:
            return {
                "success": True,
                "tx_hash": tx_hash.hex(),
                "receipt": receipt
            }
        else:
            return {
                "success": False,
                "error": "Transaction reverted",
                "tx_hash": tx_hash.hex(),
                "receipt": receipt
            }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "receipt": None
        }


print('Starting up bot...')

TOKEN: Final = os.environ['TOKEN']
BOT_USERNAME: Final = os.environ['BOT_USER']

bot = Bot(token=TOKEN)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = Session()
    id =update.message.chat.id
    user = session.query(User).filter_by(telegram_id=id).first()
    if not user:
        wallet = create_wallet_for_new_user()
        user=User(telegram_id=id, created_at=datetime.now(), wallet=wallet["wallet"], p_key=wallet["p_key"])
        session.add(user)
        session.commit()
    await update.message.reply_text('Hello there! I\'m a Njikwa.\nlet me help you stay capital discipline.\n\n'
                                    'To get started use /login to log your MT5 account in\n\n'
                                    'But you will need to subscribe to use this service, its just $5 BaseEth\n\n'
                                    'this is your wallet addy:\n\n'
                                    f'{user.wallet}\n\n/help to see all you can do\n\n'
                                    f'For support contact dev TG:@Im_definard')

async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat.id
    session = Session()

    # Ensure user exists
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, created_at=datetime.now())
        session.add(user)
        session.commit()

    if not user.subscribed:
        await update.message.reply_text("You need to subscribe to log an account use /subscribe")
        return


    user.state ="ACCOUNT_ID"
    session.commit()
    await update.message.reply_text(f"Enter your Account ID:")

async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat.id
    session = Session()

    # Ensure user exists
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, created_at=datetime.now())
        session.add(user)
        session.commit()


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('')

async def setrisk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat.id
    session = Session()

    # Ensure user exists
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, created_at=datetime.now())
        session.add(user)
        session.commit()

    await update.message.reply_text(f" Does not exist for this user.")

def handle_response(text: str) -> str:
    # Create your own response logic
    processed: str = text.lower()

    response = ''
    if "hello" in processed:
        response = "Hi there, I'm Njikwa and I am here to help you protect your funds, just /help to know all i can do\n\nThis is not an Ai bot so no need to reply this"
    else:
        response = ("I do not understand, use /help to know how to use me"
                    "\n\nGgs")


    return response

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session =Session()
    # Get basic info of the incoming message
    message_type: str = update.message.chat.type
    text: str = update.message.text
    id =update.message.chat.id
    # Print a log for debugging
    print(f'User ({id}) in {message_type}: "{text}"')

    user = session.query(User).filter_by(telegram_id=id).first()
    if not user:
        user=User(telegram_id=id, created_at=datetime.now())
        session.add(user)
        session.commit()

    # React to group messages only if users mention the bot directly
    if not user.state:
        print(message_type)
        if message_type == 'group':
            # Replace with your bot username
            if BOT_USERNAME in text:
                new_text: str = text.replace(BOT_USERNAME, '').strip()
                response: str = handle_response(new_text)
            else:
                return  # We don't want the bot respond if it's not mentioned in the group
        else:
            response: str = handle_response(text)

        # Reply normal if the message is in private
        print('Bot:', response)
        await update.message.reply_text(response)
    elif user.state == "ACCOUNT_ID":
        pass
    elif user.state == "BROKER":
        pass
    elif user.state == "PASSWORD":
        pass
        user.state = None
    session.commit()


# Log errors
async def error(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f'Update {update} caused error {context.error}')

def safe_percent_change(current, previous):
    if previous is None or previous == 0:
        return 0
    return ((current - previous) / previous) * 100


async def main():
    print("start loop tick", time.time())

    while True:
        print("Loop tick", time.time())
        session = Session()

        try:
            pass

        except Exception as e:
            print("Main loop error:", e)

        finally:
            session.close()
            print('End session')

        await asyncio.sleep(2)
def thread_worker():
    asyncio.run(main())

if __name__ == '__main__':
    worker = threading.Thread(target=thread_worker)
    worker.start()
    # asyncio.create_task(main())
    app = Application.builder().token(TOKEN).build()

    # Commands
    app.add_handler(CommandHandler('start', start_command))
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(CommandHandler('login', login_command))
    app.add_handler(CommandHandler('setrisk', setrisk_command))
    app.add_handler(CommandHandler('subscribe', subscribe_command))

    # Messages
    app.add_handler(MessageHandler(filters.TEXT, handle_message))

    # Log all errors
    app.add_error_handler(error)

    print('Polling...')
    # Run the bot
    app.run_polling(poll_interval=2)
