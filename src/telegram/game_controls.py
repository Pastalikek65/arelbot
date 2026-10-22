"""Public game controls using the Bot API's informational button types."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def disabled_button(label: str) -> InlineKeyboardButton:
    """DisabledButton is the button's sole action type; never attach a callback."""
    return InlineKeyboardButton(str(label), api_kwargs={"disabled": {}})


def with_game_help(markup: InlineKeyboardMarkup, game_key: str) -> InlineKeyboardMarkup:
    rows = [list(row) for row in markup.inline_keyboard]
    callback = f"gh:{game_key}"
    if not any(button.callback_data == callback for row in rows for button in row):
        button = InlineKeyboardButton("Nasıl oynanır?", callback_data=callback)
        if rows and len(rows[-1]) <= 2:
            rows[-1].append(button)
        else:
            rows.append([button])
    # Preserve API fields such as force_reply, which cannot change during edits.
    return InlineKeyboardMarkup(rows, api_kwargs=dict(markup.api_kwargs))
