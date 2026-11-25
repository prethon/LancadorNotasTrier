import json
import time
import re
from pathlib import Path
from typing import Tuple, List, Optional, Dict, Any
from playwright.sync_api import sync_playwright, BrowserContext, Page
from .config import (
    WHATSAPP_SESSION_DIR,
    WHATSAPP_CHAT_ALVO,
    MODO_TESTE,
    DATA_DIR,
)
from .extractor import extrair_numeros_notas
from .database import inserir_nota

CHECKPOINT_FILE = DATA_DIR / "whatsapp_checkpoint.json"


class WhatsAppMonitor:
    def __init__(self, headless: bool = False):
        self.headless = headless if not MODO_TESTE else False
        self._playwright = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    def __enter__(self):
        self.iniciar()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.fechar()

    def iniciar(self) -> None:
        """Inicializa o navegador com perfil persistente para manter a sessão salva."""
        self._playwright = sync_playwright().start()
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-default-browser-check",
            "--disable-infobars",
        ]
        self.context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(WHATSAPP_SESSION_DIR),
            headless=self.headless,
            viewport={"width": 1280, "height": 800},
            args=args,
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

    def abrir_e_garantir_login(self, timeout_login: int = 120) -> bool:
        """Acessa o WhatsApp Web e aguarda autenticação."""
        print("[WhatsApp] Acessando web.whatsapp.com...")
        self.page.goto("https://web.whatsapp.com", wait_until="load")

        seletor_painel_conversas = "#pane-side, div[aria-label='Lista de conversas'], div[role='grid']"
        seletor_qrcode = "canvas[aria-label='Scan me!'], div[data-ref]"

        tempo_inicial = time.time()
        print("[WhatsApp] Verificando sessão...")

        while time.time() - tempo_inicial < timeout_login:
            if self.page.locator(seletor_painel_conversas).count() > 0:
                print("[WhatsApp] Sessão autenticada e pronta para uso!")
                return True

            if self.page.locator(seletor_qrcode).count() > 0:
                print("[WhatsApp] Aguardando escaneamento do QR Code no navegador aberto...")
                time.sleep(3)
                continue

            time.sleep(1)

        print("[WhatsApp] Tempo limite excedido para autenticação no WhatsApp Web.")
        return False

    def selecionar_chat_alvo(self, nome_chat: Optional[str] = None) -> str:
        """Seleciona o chat desejado ou utiliza a conversa atualmente aberta."""
        chat_a_buscar = nome_chat or WHATSAPP_CHAT_ALVO

        if chat_a_buscar:
            print(f"[WhatsApp] Buscando pelo chat: '{chat_a_buscar}'...")
            campo_busca = self.page.locator("div[contenteditable='true'][data-tab='3'], div[aria-label='Pesquisar texto da caixa']")
            if campo_busca.count() > 0:
                campo_busca.first.click()
                campo_busca.first.fill(chat_a_buscar)
                self.page.keyboard.press("Enter")
                time.sleep(1.5)

        header_title = self.page.locator("header span[title], header div[role='button'] span[dir='auto']")
        nome_contato = "Chat_Ativo"
        if header_title.count() > 0:
            texto = header_title.first.inner_text().strip()
            if texto:
                nome_contato = texto

        print(f"[WhatsApp] Conversa selecionada: {nome_contato}")
        return nome_contato

    def _carregar_dados_checkpoint(self) -> Optional[Dict[str, Any]]:
        if CHECKPOINT_FILE.exists():
            try:
                with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    def _salvar_dados_checkpoint(self, dados: Dict[str, Any]) -> None:
        try:
            with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
                json.dump(dados, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[WhatsApp] Aviso ao salvar checkpoint: {e}")

    def _gerar_id_mensagem(self, elemento) -> str:
        data_id = elemento.get_attribute("data-id")
        if data_id:
            return data_id

        pre_text = elemento.get_attribute("data-pre-plain-text") or ""
        texto = elemento.inner_text().strip()
        return f"{pre_text}_{texto}"

    def ler_mensagens_e_enfileirar(self) -> Tuple[int, List[str]]:
        """Lê novas mensagens e enfileira no SQLite garantindo tolerância a falhas."""
        nome_contato = self.selecionar_chat_alvo()
        time.sleep(1)

        locators_mensagens = self.page.locator("div.message-in")
        total_elementos = locators_mensagens.count()

        if total_elementos == 0:
            print("[WhatsApp] Nenhuma mensagem recebida encontrada na conversa.")
            return 0, []

        checkpoint = self._carregar_dados_checkpoint()

        if not checkpoint:
            ids_historico_antigo = []
            for i in range(total_elementos):
                el = locators_mensagens.nth(i)
                ids_historico_antigo.append(self._gerar_id_mensagem(el))

            ultimo_id = ids_historico_antigo[-1] if ids_historico_antigo else ""
            novo_checkpoint = {
                "criado_em": time.time(),
                "ultimo_id_processado": ultimo_id,
                "ids_processados": ids_historico_antigo[-300:],
            }
            self._salvar_dados_checkpoint(novo_checkpoint)
            print("[WhatsApp] 🚀 Marco zero definido! Mensagens antigas ignoradas.")
            return 0, []

        ids_processados_set = set(checkpoint.get("ids_processados", []))
        novas_notas: List[str] = []
        ids_novos_encontrados: List[str] = []
        novo_ultimo_id = checkpoint.get("ultimo_id_processado")

        for i in range(total_elementos):
            try:
                elemento = locators_mensagens.nth(i)
                msg_id = self._gerar_id_mensagem(elemento)

                if msg_id in ids_processados_set:
                    continue

                texto = elemento.inner_text().strip()
                if not texto:
                    continue

                ids_novos_encontrados.append(msg_id)
                novo_ultimo_id = msg_id

                numeros = extrair_numeros_notas(texto)
                for num in numeros:
                    foi_inserida = inserir_nota(numero_nota=num, origem_contato=nome_contato)
                    if foi_inserida:
                        print(f"  -> [NOVA NOTA CAPTURADA] Nº {num} enfileirada no banco local.")
                        novas_notas.append(num)

            except Exception:
                continue

        if ids_novos_encontrados:
            lista_atual = checkpoint.get("ids_processados", [])
            lista_atual.extend(ids_novos_encontrados)
            checkpoint["ids_processados"] = lista_atual[-500:]
            checkpoint["ultimo_id_processado"] = novo_ultimo_id
            checkpoint["atualizado_em"] = time.time()
            self._salvar_dados_checkpoint(checkpoint)
            print(f"[WhatsApp] Varredura concluída: {len(novas_notas)} nova(s) nota(s) enfileirada(s).")
        else:
            print("[WhatsApp] Nenhuma nota nova recebida desde a última verificação.")

        return len(novas_notas), novas_notas

    def reagir_mensagem_nota_lancada(self, numero_nota: str) -> bool:
        """
        Localiza a mensagem correspondente à nota lançada no WhatsApp Web
        e reage com o emoji ✅ para confirmar à loja o lançamento bem-sucedido.
        """
        try:
            print(f"[WhatsApp] Procurando mensagem da NF {numero_nota} para reagir com ✅...")
            # Localiza a bolha de mensagem que contém o número da nota
            bolhas = self.page.locator(f"div.message-in:has-text('{numero_nota}')")
            if bolhas.count() == 0:
                return False

            alvo = bolhas.last
            alvo.hover()
            time.sleep(0.5)

            # Tenta clicar no botão de reação que surge ao passar o mouse
            btn_react = alvo.locator("button[aria-label='Reagir'], span[data-icon='react']")
            if btn_react.count() == 0:
                # Tenta pelo menu de contexto da mensagem
                btn_react = self.page.locator("span[data-icon='react'], [data-testid='reaction-button']")

            if btn_react.count() > 0:
                btn_react.first.click()
                time.sleep(0.5)
                # Seleciona o emoji de verificação ✅ (ou 👍 de fallback)
                emoji_check = self.page.locator("button[aria-label*='marcado'], button[aria-label*='positivo'], [data-emoji='✅'], [data-emoji='👍']")
                if emoji_check.count() > 0:
                    emoji_check.first.click()
                    print(f"[WhatsApp] ✅ Reação adicionada com sucesso na mensagem da NF {numero_nota}!")
                    return True

            return False
        except Exception as e:
            print(f"[WhatsApp] Aviso ao reagir à mensagem da NF {numero_nota}: {e}")
            return False

    def enviar_alerta(self, numero_destinatario: str, mensagem_alerta: str) -> bool:
        """
        Envia uma mensagem de alerta para um número de WhatsApp específico configurado na interface.
        Exemplo de número: 5511999998888 ou o nome exato do contato.
        """
        if not numero_destinatario or not numero_destinatario.strip():
            return False

        destino = numero_destinatario.strip()
        print(f"[WhatsApp - Alerta] Enviando notificação para '{destino}'...")

        try:
            # 1. Tenta buscar pelo campo de pesquisa
            campo_busca = self.page.locator("div[contenteditable='true'][data-tab='3'], div[aria-label='Pesquisar texto da caixa']")
            if campo_busca.count() > 0:
                campo_busca.first.click()
                campo_busca.first.fill(destino)
                self.page.keyboard.press("Enter")
                time.sleep(1.5)

            # 2. Caixa de digitação da mensagem
            campo_msg = self.page.locator("div[contenteditable='true'][data-tab='10'], footer div[contenteditable='true']")
            if campo_msg.count() > 0:
                campo_msg.first.click()
                campo_msg.first.fill(mensagem_alerta)
                self.page.keyboard.press("Enter")
                time.sleep(1)
                print(f"[WhatsApp - Alerta] Notificação enviada com sucesso para '{destino}'!")
                return True

            return False
        except Exception as e:
            print(f"[WhatsApp - Alerta] Erro ao enviar alerta: {e}")
            return False

    def fechar(self) -> None:
        try:
            if self.context:
                self.context.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
