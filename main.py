import asyncio
import discord
import yt_dlp
from token_project import TOKEN
from discord.ext import commands
from googletrans import Translator
import sqlite3

yt_dlp.utils.bug_reports_message = lambda: ''

ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
}

ffmpeg_options = {
    'options': '-vn',
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)


class YTDLSource(discord.PCMVolumeTransformer):  # поиск по запросу в ютубе
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)

        self.data = data

        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if 'entries' in data:
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = {}

    @commands.command()
    async def join(self, ctx, *, channel: discord.VoiceChannel):
        if ctx.voice_client is not None:
            return await ctx.voice_client.move_to(channel)

        await channel.connect()

    @commands.command()
    async def play(self, ctx, *, url):  # проигрывает трек, можно передать как ссылку, так и просто название
        async with ctx.typing():
            player = await YTDLSource.from_url(url, loop=self.bot.loop, stream=True)
            ctx.voice_client.play(player, after=lambda e: print(f'Player error: {e}') if e else self.play_next(ctx))
        await ctx.send(f'Сейчас играет: {player.title}')

    @commands.command()
    async def volume(self, ctx, volume: int):
        if ctx.voice_client is None:
            return await ctx.send("Не подключены к голосовому каналу.")

        ctx.voice_client.source.volume = volume / 100
        await ctx.send(f"Громкость изменена на {volume}%")

    @commands.command()
    async def stop(self, ctx):
        await ctx.voice_client.disconnect()

    @commands.command()
    async def pause(self, ctx):
        await ctx.voice_client.pause()
        await ctx.send('Музыка поставлена на паузу')

    @commands.command()
    async def resume(self, ctx):
        await ctx.voice_client.resume()

    @commands.command()
    async def queue(self, ctx, *, url):
        guild_id = ctx.guild.id
        if guild_id not in self.queue:
            self.queue[guild_id] = []
        self.queue[guild_id].append(url)
        await ctx.send(f"Добавлена в очередь: {url}")

    @commands.command(name='play-q')
    async def play_q(self, ctx):  # проигрывает трек, первый стоящий в очереди
        async with ctx.typing():
            player = await YTDLSource.from_url(self.queue[ctx.guild.id].pop(0), loop=self.bot.loop, stream=True)
            ctx.voice_client.play(player, after=lambda e: print(f'Ошибка плеера: {e}') if e else self.play_next(ctx))
        await ctx.send(f'Сейчас играет: {player.title}')

    @commands.command(name='q-info')
    async def q_info(self, ctx):
        q = self.queue[ctx.guild.id]
        for i in range(len(q)):
            response = str(i + 1) + '. ' + q[i]
            await ctx.send(response)

    @play.before_invoke
    @play_q.before_invoke
    async def ensure_voice(self, ctx):
        if ctx.voice_client is None:
            if ctx.author.voice:
                await ctx.author.voice.channel.connect()
            else:
                await ctx.send("Вы не подключены к голосовому каналу")
                raise commands.CommandError("Автор не подключен к голосовому каналу")
        elif ctx.voice_client.is_playing():
            ctx.voice_client.stop()

    def play_next(self, ctx):
        if ctx.guild.id in self.queue and self.queue[ctx.guild.id]:
            url = self.queue[ctx.guild.id].pop(0)
            asyncio.run_coroutine_threadsafe(self.play(ctx, url=url), self.bot.loop)
        else:
            asyncio.run_coroutine_threadsafe(ctx.send("Очередь пуста"), self.bot.loop)


# Класс для команд перевода
class TranslateBot(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.translator = Translator()

    @commands.command(name="translate")
    async def translate(self, ctx, target_language: str, *,
                        text=None):  # язык, на который хотим перевести должен быть написан полностью и на английском
        if text:
            translated = self.translator.translate(text, dest=target_language)
            await ctx.send(f"Перевод: {translated.text}")
        else:
            if ctx.message.reference and ctx.message.reference.message_id:
                referenced_message = await ctx.channel.fetch_message(ctx.message.reference.message_id)

                if referenced_message.content:
                    translated = self.translator.translate(referenced_message.content, dest=target_language)
                    await ctx.send(f"Перевод: {translated.text}")
                else:
                    await ctx.send("Сообщение пустое или недоступно для перевода.")
            else:
                await ctx.send("Не найдено сообщение, на которое ответили. Укажите текст или ответьте на сообщение.")


class Survey(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_surveys = {}

    @commands.command(name="survey")
    async def create_survey(self, ctx, *, content: str):  # !survey Вопрос? ; Опция 1, Опция 2, Опция 3

        # Проверка наличия разделителя
        if ';' not in content:
            await ctx.send("Используйте ';' чтобы разделить вопрос от опций.")
            return

        # Разделение вопроса и опций
        question, raw_options = content.split(';', 1)
        options = [opt.strip() for opt in raw_options.split(',')]

        if len(options) < 2:
            await ctx.send("Для опроса требуется хотя бы два варианта.")
            return

        if ctx.channel.id in self.active_surveys:
            await ctx.send("В этом канале уже идет опрос. Завершите его, чтобы начать новый.")
            return

        survey_message = await ctx.send(f"**Опрос:** {question}\n" +
                                        "\n".join(f"{i + 1}. {opt}" for i, opt in enumerate(options)))

        emoji_numbers = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']
        for i in range(len(options)):
            await survey_message.add_reaction(emoji_numbers[i])

        self.active_surveys[ctx.channel.id] = {
            "question": question,
            "options": list(options),
            "message_id": survey_message.id,
            "reactions": emoji_numbers[:len(options)]
        }

    @commands.command(name="end-survey")
    async def end_survey(self, ctx):
        # Завершение опроса в текущем канале
        if ctx.channel.id not in self.active_surveys:
            await ctx.send("В этом канале нет активных опросов.")
            return

        survey = self.active_surveys.pop(ctx.channel.id)
        survey_message = await ctx.channel.fetch_message(survey["message_id"])

        # Подсчет голосов
        results = {opt: 0 for opt in survey["options"]}

        for reaction in survey_message.reactions:
            if reaction.emoji in survey["reactions"]:
                idx = survey["reactions"].index(reaction.emoji)
                results[survey["options"][idx]] = reaction.count - 1

        # Отображение результатов
        result_message = "**Результаты опроса:**\n" + "\n".join(f"{opt}: {count}" for opt, count in results.items())
        await ctx.send(result_message)

    @commands.command(name="active-survey")
    async def active_survey(self, ctx):
        # Проверка, есть ли активный опрос в текущем канале
        if ctx.channel.id in self.active_surveys:
            survey = self.active_surveys[ctx.channel.id]

            await ctx.send(f"В этом канале идет опрос: {survey['question']}")
        else:
            await ctx.send("В этом канале нет активных опросов.")


class DatabaseHandler:
    def __init__(self, db_name):
        self.conn = sqlite3.connect(db_name)
        self.create_tables()

    def create_tables(self):
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS questions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question TEXT,
                    answer TEXT
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trivia_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    score INTEGER
                )
                """
            )

    def add_question(self, question, answer):
        with self.conn:
            self.conn.execute(
                "INSERT INTO questions (question, answer) VALUES (?, ?)",
                (question, answer)
            )

    def get_random_question(self):
        cursor = self.conn.execute(
            "SELECT * FROM questions ORDER BY RANDOM() LIMIT 1"
        )
        return cursor.fetchone()

    def save_trivia_result(self, name, score):
        with self.conn:
            self.conn.execute(
                "INSERT INTO trivia_scores (name, score) VALUES (?, ?)",
                (name, score)
            )

    def get_top_scores(self, limit=10):
        cursor = self.conn.execute(
            "SELECT * FROM trivia_scores ORDER BY score DESC LIMIT ?",
            (limit,)
        )
        return cursor.fetchall()


# Класс для управления викториной
class TriviaGame:
    def __init__(self, db_handler):
        self.db_handler = db_handler
        self.active = False
        self.current_question = None
        self.correct_answer = None
        self.scores = {}

    async def start(self, ctx):
        self.active = True
        await ctx.send("Викторина началась! Готовы?")
        while self.active:
            await self.ask_question(ctx)
            await asyncio.sleep(5)  # небольшая пауза между вопросами

    async def ask_question(self, ctx):
        self.current_question = self.db_handler.get_random_question()
        if not self.current_question:
            await ctx.send("Вопросы закончились. Викторина завершена.")
            self.active = False
            return

        question, self.correct_answer = self.current_question[1], self.current_question[2]
        await ctx.send(f"Вопрос: {question}")

        # Таймер для приема ответов
        await self.accept_answers(ctx)

    async def accept_answers(self, ctx):
        def check(m):
            return m.channel == ctx.channel and not m.author.bot

        try:
            msg = await ctx.bot.wait_for("message", check=check, timeout=30)
            if msg.content.lower() == self.correct_answer.lower():
                winner = msg.author
                await ctx.send(f"{winner.mention} ответил правильно! Верный ответ: {self.correct_answer}")

                # Обновляем баллы
                if winner.id not in self.scores:
                    self.scores[winner.id] = 0
                self.scores[winner.id] += 1
            else:
                await ctx.send("Неправильный ответ.")

        except asyncio.TimeoutError:
            await ctx.send(f"Время вышло! Верный ответ: {self.correct_answer}")

    async def end(self, ctx):
        self.active = False
        await ctx.send("Викторина завершена.")
        for player_id, score in self.scores.items():
            user = await ctx.bot.fetch_user(player_id)
            self.db_handler.save_trivia_result(user.name, score)

    def is_active(self):
        return self.active


class TriviaBot(commands.Cog):
    def __init__(self, bot, db_handler, trivia_game):
        self.bot = bot
        self.db_handler = db_handler
        self.trivia_game = trivia_game

    @commands.command(name="start-trivia")
    async def start_trivia(self, ctx):
        if self.trivia_game.is_active():
            await ctx.send("Викторина уже идет.")
        else:
            await self.trivia_game.start(ctx)

    @commands.command(name="end-trivia")
    async def end_trivia(self, ctx):
        if self.trivia_game.is_active():
            await self.trivia_game.end(ctx)
        else:
            await ctx.send("Викторина уже завершена.")

    @commands.command(name="add-question")
    async def add_question(self, ctx, *, input_data):
        question, answer = input_data.split(";")
        self.db_handler.add_question(question.strip(), answer.strip())
        await ctx.send("Вопрос добавлен.")

    @commands.command(name="show-leader")
    async def show_leader(self, ctx):
        top_scores = self.db_handler.get_top_scores()
        response = "Топ 10 результатов:\n"
        for score in top_scores:
            response += f"{score[1]}: {score[2]} баллов\n"
        await ctx.send(response)


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(
    command_prefix=commands.when_mentioned_or("!"),
    description='Relatively simple music bot example',
    intents=intents,
)

db_handler = DatabaseHandler("trivia.db")
trivia_game = TriviaGame(db_handler)


@bot.command(name='commands')
async def commands__(ctx):
    commands_ = {
        '!play': "начинает играть музыку, если вы находитесь в канале. Принимает как ссылку, так и просто название",
        '!stop': "бот перестает играть музыку и выходит из канала",
        '!pause': "ставит музыку на паузу",
        '!resume': "продолжает играть музыку",
        '!volume': "устанавливает определенную громкость, передается целое число",
        '!queue': 'добавляет трек в очередь',
        '!q-info': 'выводит список треков в очереди',
        '!play-q': 'проигрывает трек, первый стоящий в очереди',
        '!translate': 'переводит сообщение на выбранны язык, {язык, название на английском} {сообщение}',
        '!survey': 'устраивает опрос, !survey Вопрос? | Опция 1, Опция 2, Опция 3',
        '!end-survey': 'заканчивает опрос',
        '!active-survey': 'выводит активный опрос',
        '!start-trivia': 'начинает викторину',
        "!end-trivia": 'заканчивает викторину',
        '!add-question': 'добавляет вопрос и ответ в бд, {вопрос}; {ответ}',
        '!show-leader': 'выводит таблицу лидеров',
    }
    for elem in commands_:
        await ctx.send(f'{elem} - {commands_[elem]}')


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    await bot.process_commands(message)


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')


async def main():
    async with bot:
        await bot.add_cog(Music(bot))
        await bot.add_cog(TranslateBot(bot))
        await bot.add_cog(Survey(bot))
        await bot.add_cog(TriviaBot(bot, db_handler, trivia_game))
        await bot.start(TOKEN)


asyncio.run(main())
