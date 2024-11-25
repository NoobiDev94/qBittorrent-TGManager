import ssl
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, CallbackContext
import os
import platform
import time
import psutil
import shutil
import subprocess
import glob
from qbittorrentapi import Client, TorrentState
from dotenv import load_dotenv

load_dotenv()
client = Client()

# Configurações do Telegram e qBit
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
FILE_CHAT_ID= os.getenv("TELEGRAM_FILE_CHAT_ID")
DOWNLOADS_PATH = os.getenv("DOWNLOADS_PATH")
QB_USERNAME = os.getenv("QB_USERNAME")
QB_PASSWORD = os.getenv("QB_PASSWORD")
QB_HOST = os.getenv("QB_HOST")

# Armazena os IDs das mensagens dos torrents e últimos tempos de seeding
torrent_message_ids = {}
torrent_last_uploaded = {}

# Mensagens globais
DOWNLOAD_MESSAGE_TEMPLATE = """\
Name: {torrent_name}
Status: {status}
[{progress_bar}] {progress:.2f}%
Processed: {downloaded:.2f}GB of {total_size:.2f}GB
Speed: {dlspeed:.2f} MB/s | ETA: {eta}
Time Elapsed: {time_elapsed}

CPU: {cpu_percent}% | FREE: {free_space_gb:.2f}GB
RAM: {ram_percent}% | UPTIME: {uptime}
DL: {dlspeed:.2f} MB/s | UL: {upspeed:.2f} MB/s
TAG: {tag} | RATIO: {ratio:.2f}
UPLOADED: {uploaded:.2f}GB
"""

SEEDING_MESSAGE_TEMPLATE = """\
Name: {torrent_name}
Status: seeding
[{progress_bar}] {progress:.2f}%
Processed: {downloaded:.2f}GB of {total_size:.2f}GB

CPU: {cpu_percent}% | FREE: {free_space_gb:.2f}GB
RAM: {ram_percent}% | UPTIME: {uptime}
DL: {dlspeed:.2f} MB/s | UL: {upspeed:.2f} MB/s
TAG: {tag} | RATIO: {ratio:.2f}
UPLOADED: {uploaded:.2f}GB
"""

# Verifica se todas as variáveis de ambiente estão presentes
if not QB_HOST:
    print("Erro: QB_HOST não está definido.")
if not QB_USERNAME:
    print("Erro: QB_USERNAME não está definido.")
if not QB_PASSWORD:
    print("Erro: QB_PASSWORD não está definido.")

# Checa a conexão com o qBit
def connect_to_qbittorrent():
    try:
        print(f"Tentando conectar ao qBittorrent em {QB_HOST}...")
        qbt = Client(host=QB_HOST, username=QB_USERNAME, password=QB_PASSWORD)
        qbt.auth_log_in()
        print("Conexão estabelecida com sucesso!")
        return qbt
    except Exception as e:
        print(f"Erro ao conectar ao qBittorrent: {str(e)}")
        return None

# Função para obter espaço livre do HD onde o qBit está configurado
def get_free_space_from_qbittorrent(qbt):
    try:
        main_data = qbt.sync.maindata()
        free_space_bytes = main_data.get('server_state', {}).get('free_space_on_disk', None)
        if free_space_bytes is not None:
            return free_space_bytes / (1024 ** 3)
        return "Indisponível"
    except Exception as e:
        print(f"Erro ao obter espaço livre do qBittorrent: {e}")
        return "Erro ao acessar API"

# Enviar ou editar mensagem no Telegram
async def send_or_edit_message(bot, message, torrent_name):
    if torrent_name in torrent_message_ids:
        # Edita a mensagem se ela já existe
        message_id = torrent_message_ids[torrent_name]
        await bot.edit_message_text(chat_id=CHAT_ID, message_id=message_id, text=message)
    else:
        # Envia uma nova mensagem e armazena o ID
        sent_message = await bot.send_message(chat_id=CHAT_ID, text=message)
        torrent_message_ids[torrent_name] = sent_message.message_id

# Função para converter os segundos
def format_time(seconds):
    return time.strftime("%H:%M:%S", time.gmtime(seconds))

ssl._create_default_https_context = ssl._create_unverified_context

# Função que trata o comando /start
async def start_download(update: Update, context: CallbackContext):
    print("Comando /start recebido")
    await update.message.reply_text("Bot iniciado com sucesso!")

    # Tenta conectar ao qBit
    qbt = connect_to_qbittorrent()
    if qbt is None:
        await send_or_edit_message(update.message.bot, "Erro ao conectar ao qBittorrent.", "")
        return

    # Inicia o monitoramento dos torrents
    print("Iniciando monitoramento dos torrents")
    job = context.job_queue.run_repeating(monitor_torrents, interval=7, first=0, data=qbt)

# Função para monitorar o status dos torrents
async def monitor_torrents(context: CallbackContext):
    print("Executando monitoramento dos torrents...")
    qbt = context.job.data

    if qbt is None:
        print("qBittorrent não conectado!")
        return

    torrents = qbt.torrents_info()
    free_space_gb = get_free_space_from_qbittorrent(qbt)

    for torrent in torrents:
        # Verifica se o torrent está sem atividade de upload há mais de 5s
        if torrent.upspeed == 0 and torrent.state == 'stalledUP':
            last_uploaded = torrent_last_uploaded.get(torrent.name, 0)
            #print(f"Monitorando inatividade do torrent: {torrent.name}")
            #print(f"Tempo atual: {time.time()}, Último upload: {last_uploaded}, Diferença: {time.time() - last_uploaded}")

            if torrent.name in torrent_message_ids:
                if time.time() - last_uploaded > 15:  # segundos
                    message_id = torrent_message_ids[torrent.name]
                    print(f"Excluindo mensagem para o torrent {torrent.name} devido à inatividade de upload.")
                    if message_id:
                        await context.bot.delete_message(chat_id=CHAT_ID, message_id=message_id)
                    del torrent_message_ids[torrent.name]  # Remove a mensagem do dicionário
                    del torrent_last_uploaded[torrent.name]  # Remove a entrada do dicionário
                else:
                    # Atualiza last_uploaded se o torrent voltou a ter atividade temporária
                    torrent_last_uploaded[torrent.name] = time.time()
            else:
                # print(f"Registrando o tempo inicial de inatividade para '{torrent.name}'.")
                torrent_last_uploaded[torrent.name] = time.time()

        # Processa torrents que estão baixando ou pausados
        if torrent.state in ["downloading", "stoppedDL", "queuedDL"]:
            eta = format_time(torrent.eta) if torrent.eta > 0 else "N/A"
            elapsed = format_time(torrent.time_active)

            message = DOWNLOAD_MESSAGE_TEMPLATE.format(
                torrent_name=torrent.name,
                status=torrent.state,
                progress_bar=f"{int(torrent.progress * 10) * '▰'}{(10 - int(torrent.progress * 10)) * '▱'}",
                progress=torrent.progress * 100,
                downloaded=torrent.downloaded / (1024 ** 3),
                total_size=torrent.total_size / (1024 ** 3),
                dlspeed=torrent.dlspeed / (1024 ** 2),
                eta=eta,
                time_elapsed=elapsed,
                cpu_percent=psutil.cpu_percent(),
                free_space_gb=free_space_gb,
                ram_percent=psutil.virtual_memory().percent,
                uptime=format_time(time.time() - psutil.boot_time()),
                upspeed=torrent.upspeed / (1024 ** 2),
                tag=torrent.tags,
                ratio=torrent.ratio,
                uploaded=torrent.uploaded / (1024 ** 3)
            )
            await send_or_edit_message(context.bot, message, torrent.name)

        # Exibe status de seeding como uma atualização da mensagem de download
        elif torrent.upspeed > 0:
            elapsed = format_time(torrent.time_active)

            message = SEEDING_MESSAGE_TEMPLATE.format(
                torrent_name=torrent.name,
                progress_bar=f"{int(torrent.progress * 10) * '▰'}{(10 - int(torrent.progress * 10)) * '▱'}",
                progress=torrent.progress * 100,
                downloaded=torrent.downloaded / (1024 ** 3),
                total_size=torrent.total_size / (1024 ** 3),
                cpu_percent=psutil.cpu_percent(),
                free_space_gb=free_space_gb,
                ram_percent=psutil.virtual_memory().percent,
                uptime=format_time(time.time() - psutil.boot_time()),
                dlspeed=torrent.dlspeed / (1024 ** 2),
                upspeed=torrent.upspeed / (1024 ** 2),
                tag=torrent.tags,
                ratio=torrent.ratio,
                uploaded=torrent.uploaded / (1024 ** 3)
            )
            await send_or_edit_message(context.bot, message, torrent.name)
        # Verificação de finalização de download
        if torrent.state == "stalledUP" and torrent.progress == 1.0:
            print(f"Download concluído para '{torrent.name}'. Iniciando compactação e envio.")
            await send_completed_torrent_parts(context, torrent.name, os.path.join(DOWNLOADS_PATH, torrent.name))


        # Armazena o tempo do último upload se há atividade de upload
        if torrent.upspeed > 0:
            print(f"Atualizando o tempo de upload ativo para '{torrent.name}'.")
            torrent_last_uploaded[torrent.name] = time.time()

# Função para dividir e enviar arquivos compactados
async def send_completed_torrent_parts(context, torrent_name, files_path):
    part_pattern = None #Para função inteira ter acesso a variavel.
    try:
        # Cria uma pasta temporária para o torrent
        temp_folder = os.path.join("/tmp", torrent_name.replace(" ", "_"))
        os.makedirs(temp_folder, exist_ok=True)

        # Copia os arquivos para a pasta temporária
        for file in os.listdir(files_path):
            original_path = os.path.join(files_path, file)
            if os.path.isfile(original_path):
                shutil.copy2(original_path, temp_folder)

        # Compacta a pasta em partes de 2GB
        archive_name = os.path.join("/tmp", f"{torrent_name.replace(' ', '_')}")
        print("Iniciando compactação em partes")

        # Substituir o comando de split e tar por algo mais seguro
        split_command = f"tar -czf - -C /tmp {torrent_name.replace(' ', '_')} | split -b 2GB - {archive_name}.part"

        # Executar o comando com subprocess
        process = subprocess.Popen(split_command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate()

        # Verifique se houve erros durante a execução do comando
        if process.returncode != 0:
            print(f"Erro ao executar o comando de compactação/divisão: {stderr.decode()}")
            return

        # Envia cada parte para o canal no Telegram
        part_pattern = f"{archive_name}.part*"
        part_num = 1
        for part_file in sorted(glob.glob(part_pattern)):
            with open(part_file, "rb") as file_part:
                await context.bot.send_document(chat_id=FILE_CHAT_ID, document=InputFile(file_part),
                                                caption=f"{torrent_name} - Parte {part_num}")
            part_num += 1

        # Envia uma mensagem final de confirmação
       
        await context.bot.send_message(chat_id=FILE_CHAT_ID,
                                       text=f"O torrent '{torrent_name}' foi compactado e enviado com sucesso.")
    except subprocess.CalledProcessError as e:
        print(f"Erro ao executar o comando de compactação/divisão: {e}")
    except Exception as ex:
        print(f"Erro durante o envio das partes: {ex}")
    finally:
        # Remove arquivos temporários
        shutil.rmtree(temp_folder, ignore_errors=True)
        if part_pattern:  # Verificar se part_pattern foi definido
            for part_file in glob.glob(part_pattern):
                os.remove(part_file)

# Função principal que configura e inicia o bot
def main():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_download))
    application.run_polling()
    print("Bot iniciado.")

if __name__ == "__main__":
    print("Python iniciado.")
    main()
