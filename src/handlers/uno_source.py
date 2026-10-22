"""Private corresponding-source delivery for the source-derived UNO module."""
from telegram import InputFile
from src.services.uno_source_offer import UnoSourceOffer


async def deliver_source(update, context):
    if update.effective_chat.type != "private":
        return
    message = update.effective_message
    try:
        async for name, data in UnoSourceOffer().parts():
            await message.reply_document(InputFile(data, filename=name))
        await message.reply_text(
            "Bu sürümün tam kaynak arşivi. Parçaları numara sırasıyla birleştirip tar.gz olarak aç. "
            "Örnek: cat arel-source.tar.gz.part* > arel-source.tar.gz\n"
            "Kaynak UNO bileşeni AGPL-3.0-or-later; telif ve lisans dosyaları arşivdedir."
        )
    except (OSError, ValueError, KeyError):
        await message.reply_text("Kaynak arşivi şu anda okunamadı. Daha sonra /uno kaynak ile tekrar dene.")
