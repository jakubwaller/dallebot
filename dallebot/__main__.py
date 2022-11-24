import datetime
import html
import logging
import os
import traceback

import openai as openai
import pandas as pd
from telegram import Update, ParseMode, ChatAction
from telegram.ext import Updater, CallbackContext, CommandHandler, ConversationHandler, MessageHandler, Filters

from tools import read_config

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

min_requests_delay = 60  # in seconds
max_images_per_day = 5
csv_file_name = "logs/dalle_bot_logs.csv"
df_columns = ["group", "timestamp", "prompt", "size", "hashed_user"]

config = read_config()
developer_chat_id = config["developer_chat_id"]
bot_token = config["bot_token"]
openai_api_key = config["openai_api_key"]

openai.api_key = openai_api_key

MESSAGE, EMPTY_MESSAGE = range(2)

try:
    df = pd.read_csv(csv_file_name)
    df = df.astype({"timestamp": "datetime64"})
except Exception:
    df = pd.DataFrame(columns=df_columns)
    outdir = "logs"
    if not os.path.exists(outdir):
        os.mkdir(outdir)


def start(update: Update, context: CallbackContext) -> int:
    context.bot.send_message(
        update.message.chat.id,
        "Hi there! I’m DALL·E Bot.\n"
        "Send me a prompt and I’ll send you an image generated by OpenAI’s DALL·E.\n"
        f"As the image generation is not for free there is a limit set to "
        f"one request per {min_requests_delay} seconds and {max_images_per_day} images per day.\n"
        f"In order to achieve this, I'm storing your anonymised hashed user id together with "
        f"the timestamp of your message.\n"
        f"To comply with OpenAI's moderation policy, I'm also storing the prompts and the generated images, "
        f"again all fully anonymised.\n\n"
        f"If you find issues or have any questions, please contact dallebot@jakubwaller.eu\n"
        f"If you want to support the bot, you can buy him a coffee here https://ko-fi.com/jakubwaller\n"
        f"Feel free to also check out the code at: https://github.com/jakubwaller/dallebot",
    )

    return MESSAGE


def generate(
    update: Update,
    context: CallbackContext,
    prompt: str,
    chat_id: int,
    datetime_now: datetime.datetime,
    hashed_user: int,
    size: int = 256,
) -> int:
    """Sends a dalle image."""
    context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    global df

    try:
        if "group" in update.message.chat.type:
            is_group = True
        else:
            is_group = False
    except Exception as e:
        logger.error(e)
        is_group = False

    df = pd.concat([df, pd.DataFrame([[is_group, datetime_now, prompt, size, hashed_user]], columns=df_columns)])
    df.to_csv(csv_file_name, header=True, index=False)

    if is_group:
        is_group_text = "group: "
    else:
        is_group_text = "single user: "

    try:
        moderation_response = openai.Moderation.create(prompt)

        if not moderation_response["results"][0]["flagged"]:
            response = openai.Image.create(prompt=prompt, n=1, size=f"{size}x{size}", user=str(hashed_user))
            image_url = response["data"][0]["url"]

            context.bot.send_photo(chat_id, image_url, caption=prompt)
            context.bot.send_photo(developer_chat_id, image_url, caption=is_group_text + prompt)
        else:
            context.bot.send_message(chat_id, "This prompt doesn't comply with OpenAI's content policy.")
            context.bot.send_message(
                developer_chat_id, f"This prompt doesn't comply with OpenAI's content policy: " f"{prompt}."
            )
    except (openai.error.InvalidRequestError, openai.error.RateLimitError) as e:
        context.bot.send_message(chat_id, str(e))
        context.bot.send_message(developer_chat_id, f"{prompt}\n{str(e)}")
    except Exception as e:
        raise e

    return MESSAGE


def generate_from_command(update: Update, context: CallbackContext) -> int:
    """Checks if there is a prompt."""
    prompt = (" ".join(context.args)).strip()

    return check_if_prompt_empty_and_message_not_too_early(update, context, prompt)


def generate_from_message(update: Update, context: CallbackContext) -> int:
    """Previous command didn't include a prompt, let's see if this one does."""
    prompt = update.message.text.strip()

    return check_if_prompt_empty_and_message_not_too_early(update, context, prompt)


def check_if_prompt_empty_and_message_not_too_early(update: Update, context: CallbackContext, prompt) -> int:
    global df

    chat_id = update.message.chat.id
    hashed_user = hash(update.message.from_user.id)
    datetime_now = datetime.datetime.now()
    max_datetime_for_user = max(
        df[df.hashed_user == hashed_user]["timestamp"], default=datetime.datetime.strptime("2022-01-01", "%Y-%m-%d")
    )
    seconds_diff = (datetime_now - max_datetime_for_user).seconds

    if seconds_diff < min_requests_delay:
        context.bot.send_message(
            chat_id,
            f"Sorry, due to resource constraints, it's only allowed to send one request per "
            f"{min_requests_delay} seconds.\n"
            f"Please try again in {min_requests_delay - seconds_diff} seconds.",
        )

        return EMPTY_MESSAGE

    today = datetime.datetime.combine(datetime.date.today(), datetime.datetime.min.time())
    number_of_requests_per_day = df[df.hashed_user == hashed_user][df.timestamp >= today].shape[0]

    if number_of_requests_per_day > max_images_per_day:
        context.bot.send_message(
            chat_id,
            f"Sorry, as the image generation is not for free, there is a limit of {max_images_per_day} per day. "
            f"Please try again tomorrow.",
        )

        return EMPTY_MESSAGE

    if len(prompt) == 0 or prompt == "":
        context.bot.send_message(chat_id, "K let's do this! What image should I generate?")
        return EMPTY_MESSAGE
    else:
        return generate(update, context, prompt, chat_id, datetime_now, hashed_user)


def error_handler(update: object, context: CallbackContext) -> int:
    """Log the error and send a telegram message to notify the developer."""
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)

    message = f"An exception was raised while handling an update\n" f"<pre>{html.escape(tb_string)}"

    message = message[:4090] + "</pre>"

    context.bot.send_message(chat_id=developer_chat_id, text=message, parse_mode=ParseMode.HTML)

    return MESSAGE


def cancel(update: Update, context: CallbackContext) -> int:
    """Cancels and ends the conversation."""

    return MESSAGE


def main() -> None:
    """Setup and run the bot."""
    # Create the Updater and pass it your bot's token.
    updater = Updater(bot_token)

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("generate", generate_from_command, pass_args=True),
            CommandHandler("start", start),
        ],
        states={
            MESSAGE: [
                CommandHandler("generate", generate_from_command, pass_args=True),
                CommandHandler("start", start),
            ],
            EMPTY_MESSAGE: [
                MessageHandler(Filters.text & ~Filters.command, generate_from_message),
                CommandHandler("generate", generate_from_command, pass_args=True),
                CommandHandler("start", start),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    updater.dispatcher.add_handler(conv_handler)

    updater.dispatcher.add_error_handler(error_handler)

    # Start the Bot
    updater.start_polling(poll_interval=1)

    # Run the bot until the user presses Ctrl-C or the process receives SIGINT,
    # SIGTERM or SIGABRT
    updater.idle()


if __name__ == "__main__":
    main()
