import time
from enum import Enum
import uuid
from kafka import KafkaConsumer
from kafka import KafkaProducer

import json
import logging
from queue import Queue
from threading import Thread
from threading import Lock
from flask import Flask, render_template, session, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room, \
    close_room, rooms

from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import MessageHandler, Filters, Dispatcher, CallbackQueryHandler
from telegram.ext import CommandHandler

import kafka_config
from chat_manager.chat_manager import ChatManager
from mq.from_ai_message import from_ai_message
# from mq.kafka.kafka_consumer import KafkaConsumer
# from mq.kafka.kafka_publisher import KafkaPublisher
from mq.to_ai_message import to_ai_message
from response_model.response_model import ResponseModel, FINAL_ANSWER

async_mode = "gevent"
app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
thread = None
thread_lock = Lock()
socketio = SocketIO(app, async_mode=async_mode)
TOKEN = '628583227:AAG4wXkmXI_nGl2x0MOjBKLJDA229FULcQU'
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                     level=logging.INFO)

logger = logging.getLogger("logger")
handlerFile = logging.FileHandler("compliance.log")
handlerConsole = logging.StreamHandler()
logger.setLevel(logging.DEBUG)
logger.addHandler(handlerFile)
logger.addHandler(handlerConsole)

rm = ResponseModel()
bot = Bot(TOKEN)
update_queue = Queue()
dispatcher = Dispatcher(bot, update_queue)


def patch_msg_data(data):
    return data.encode("ISO-8859-1").decode()


def start(bot, update):
    bot.send_message(chat_id=update.message.chat_id, text=f"Здравствуйте! Я виртуальный помощник Сбербанка "
                                                          f"Скажите пожалуйста чем вы обеспокоены")
    """
    keyboard = [[InlineKeyboardButton("Жалоба", callback_data='сбер плохо себя вел'),
                 InlineKeyboardButton("Другое", callback_data='другое')]]

    reply_markup = InlineKeyboardMarkup(keyboard)
    """
    manager.start_user_chat(update.message.chat_id)
    socketio.emit('my_response',
                  {'data': 'Server generated VERY SPECIAL event', 'count': 0},
                  namespace='/test')
    #update.message.reply_text('Please choose:', reply_markup=reply_markup)


def button(bot, update):
    query = update.callback_query
    bot.send_message(chat_id=query.message.chat_id, text=query.data)
    process_text(query.message.chat_id, query.data, bot)
    bot.send_message(chat_id=query.message.chat_id, text=query.data)



def make_to_message(text, chatid):
    return to_ai_message(messageId=str(uuid.uuid1()), userId=chatid, chatId=chatid, message=text)

def process_text(chat_id, text, bot):
    room = manager.chat_room(chat_id)
    socketio.emit('broad_response', {'chat_id': chat_id, 'room_id': room},
                  namespace="/test",
                  broadcast=True)
    socketio.emit('my_response', {'data': f'{text}', 'count': 0},
                  namespace="/test",
                  room=str(room))
    if manager.is_bot_session(chat_id):
        manager.store_message_to_bot(text, chat_id)
        message_to_bot_str = json.dumps(make_to_message(text, chat_id))
        logger.info("Try to send to publisher queue: {}.".format(message_to_bot_str))
        push(message_to_bot_str)


def userinput(bot, update):
    chatid = update.message.chat_id
    manager.start_user_chat(chatid)
    process_text(chatid, update.message.text, bot)

start_handler = CommandHandler('start', start)
user_handler = MessageHandler(None, userinput)
#dispatcher.add_handler(CallbackQueryHandler(button))
dispatcher.add_handler(start_handler)
dispatcher.add_handler(user_handler)
thread_bot = Thread(target=dispatcher.start, name='dispatcher')
thread_bot.start()


manager = ChatManager()

logger = logging.getLogger()
consumer = KafkaConsumer(kafka_config.FROM_AI_TOPIC, bootstrap_servers=kafka_config.KAFKA_SOCKET)
publisher = KafkaProducer(bootstrap_servers=kafka_config.KAFKA_SOCKET)


def _filter_user_messages(messages):
    user_message_string = ""

    for message in messages:
        if message["message_name"] == "ANSWER_TO_USER":
            user_message_string += message["payload"]["answer"]
    return user_message_string


def receive_from_bot(from_bot_message):
    print(from_bot_message)
    print(type(from_bot_message))
    chatid = from_bot_message["uuid"]["chatId"]
    room = manager.chat_room(chatid)

    messages = from_bot_message["messages"]
    user_message_string = _filter_user_messages(messages)
    all_messages_str = json.dumps(from_bot_message)
    print("BOT ANSWERED!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!: {}".format(user_message_string))

    manager.store_message_from_bot(user_message_string, chatid)
    bot.send_message(chat_id=chatid, text=user_message_string)
    socketio.emit('my_response', {'data': f'{user_message_string}', 'count': 0},
                  namespace="/test",
                  room=str(room))




def receive_from_bot(from_bot_message):
    chatid = from_bot_message["uuid"]["chatId"]
    room = manager.chat_room(chatid)

    messages = from_bot_message["messages"]
    user_messages = _filter_user_messages(messages)

    user_messages_str = user_messages

    manager.store_message_from_bot(user_messages_str, chatid)
    bot.send_message(chat_id=chatid, text=user_messages_str)
    socketio.emit('my_response', {'data': f'{user_messages_str}', 'count': 0},
                  namespace="/test",
                  room=str(room))


def push(msg):
    logger.info("Received push queue. sending to AI:")
    print("test sending")
    bmsg = msg.encode()
    publisher.send("toAI", bmsg)

@app.route('/')
def index():
    return render_template('index.html', async_mode=socketio.async_mode)

@app.route("/kafka", methods=['POST'])
def kakla_poll():
    receive_from_bot(json.loads(request.data))
    return "OK"


@app.route('/admin')
def webhook():
    bot.set_webhook("https://obscure-dawn-33815.herokuapp.com/" + TOKEN)
    return "WebHOOK connected"


@app.route('/'+TOKEN, methods=['POST', 'GET'])
def foo():
    print("webhook callback")
    update = Update.de_json(json.loads(request.data), bot)
    update_queue.put(update)
    return "OK"


@socketio.on('my_event', namespace='/test')
def test_message(message):
    session['receive_count'] = session.get('receive_count', 0) + 1
    emit('my_response',
         {'data': patch_msg_data(message['data'])})


@socketio.on('join', namespace='/test')
def join(message):
    room = int(message['room'])
    if manager.operator_join_room(room, request.sid):
        chatid = manager.chat_id(room)
        history = manager.dump_history(chatid)
        join_room(message['room'])
        emit('my_response',
             {'data': 'In rooms: ' + ', '.join(rooms())})
        if history:
            emit('my_response',
                 {'data': history})
    else:
        emit('my_response',
             {'data': 'Cannot join non-existent room'})


@socketio.on('leave', namespace='/test')
def leave(message):
    room = int(message['room'])
    if manager.operator_leave_room(room, request.sid):
        leave_room(message['room'])
        emit('my_response',
            {'data': 'In rooms: ' + ', '.join(rooms())})
    else:
        emit('my_response',
             {'data': 'Cannot delete operator from this room'})


@socketio.on('close_room', namespace='/test')
def close(message):
    #TODO - no bot logic here
    session['receive_count'] = session.get('receive_count', 0) + 1
    emit('my_response', {'data': 'Room ' + message['room'] + ' is closing.'},
         room=message['room'])
    close_room(message['room'])


@socketio.on('my_room_event', namespace='/test')
def send_room_message(message):
    room = int(message['room'])
    emit('my_response',
         {'data': patch_msg_data(message['data'])},
         room=message['room'])
    chatid = manager.chat_id(room)
    manager.store_message(patch_msg_data(message['data']), chatid, 2)
    bot.send_message(chat_id=chatid, text=patch_msg_data(message['data']))
    manager.close_bot_session(chatid)


@socketio.on('my_ping', namespace='/test')
def ping_pong():
    emit('my_pong')


@socketio.on('connect', namespace='/test')
def test_connect():
    manager.operator_chats.append(request.sid)
    emit('my_response', {'data': 'Connected', 'count': 0})
    for it in manager.chat_id_to_room_links:
        emit('broad_response_connect', {'chat_id': it["chat_id"], 'room_id': it['room_id']})


@socketio.on('disconnect', namespace='/test')
def test_disconnect():
    print('Client disconnected', request.sid)


if __name__=="__main__":
    print(Participant.OPERATOR)