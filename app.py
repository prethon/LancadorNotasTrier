import os
import sys
import time
import queue
import threading
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

from playwright.sync_api import sync_playwright
from src.config import (
    REPORTS_DIR,
    MODO_TESTE,
    LIMITE_NOTAS_POR_EXECUCAO,
    INTERVALO_MONITORAMENTO_SEGUNDOS,
    ERP_URL,
    ERP_USUARIO,
    ERP_SENHA,
    WHATSAPP_ALERTA,
    WHATSAPP_IGNORAR,
    salvar_env,
)
from src.database import (
    init_db,
    obter_resumo_estatisticas,
    obter_notas_pendentes,
    inserir_nota,
    gerar_id_unico,
    get_connection,
)
from src.erp import ERPLancador
from src.whatsapp import WhatsAppMonitor
from src.backup import executar_backup
from src.updater import (
    obter_versao_local,
    verificar_atualizacao,
    baixar_e_aplicar_atualizacao,
    reiniciar_aplicacao,
)
from main import etapa_capturar_whatsapp, etapa_lancar_erp, etapa_confirmar_whatsapp, zerar_checkpoint_whatsapp


class RedirecionadorTexto:
    """Redireciona stdout e stderr para uma fila consumida com segurança pela GUI."""
    def __init__(self, fila_texto: queue.Queue):
        self.fila_texto = fila_texto

    def write(self, texto):
        self.fila_texto.put(texto)

    def flush(self):
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()

        init_db()

        self.title("Lançador de Notas Fiscais - Painel de Controle")
        self.geometry("1100x650")
        self.minsize(960, 480)

        # Variáveis de controle
        self.thread_execucao = None
        self.evento_parar = threading.Event()
        self.fila_logs = queue.Queue()
        self.esta_executando = False

        # Variáveis da Central ERP & Acesso
        self.var_erp_url = tk.StringVar(value=ERP_URL)
        self.var_erp_usuario = tk.StringVar(value=ERP_USUARIO)
        self.var_erp_senha = tk.StringVar(value=ERP_SENHA)
        self.mostrar_senha = False

        # Variável para Notificações e Exclusões no WhatsApp
        self.var_whatsapp_alerta = tk.StringVar(value=WHATSAPP_ALERTA)
        self.var_whatsapp_ignorar = tk.StringVar(value=WHATSAPP_IGNORAR)

        # Variável para Teste Individual de Nota
        self.var_nota_teste = tk.StringVar(value="")

        # Variáveis de Execução
        self.var_modo_teste = tk.BooleanVar(value=MODO_TESTE)
        self.var_limite_notas = tk.IntVar(value=LIMITE_NOTAS_POR_EXECUCAO)
        self.var_intervalo = tk.IntVar(value=INTERVALO_MONITORAMENTO_SEGUNDOS)

        # Informações de Versão e Auto-Update
        self.info_versao = obter_versao_local()
        self.versao_atual = self.info_versao.get("versao", "1.0.0")

        # Redirecionar prints do Python para os logs da janela
        self.stdout_original = sys.stdout
        self.stderr_original = sys.stderr
        sys.stdout = RedirecionadorTexto(self.fila_logs)
        sys.stderr = RedirecionadorTexto(self.fila_logs)

        self._construir_interface()
        self._atualizar_metricas()
        self._processar_fila_logs()

        self.protocol("WM_DELETE_WINDOW", self._ao_fechar)

    def _construir_interface(self):
        # 1. Barra de Status Superior
        frame_status = ttk.Frame(self, padding=(15, 8))
        frame_status.pack(fill=tk.X)

        self.lbl_status_circulo = tk.Label(frame_status, text="●", font=("Arial", 16), fg="#dc3545")
        self.lbl_status_circulo.pack(side=tk.LEFT, padx=(0, 6))

        self.lbl_status_texto = ttk.Label(
            frame_status,
            text="Automação Parada",
            font=("Arial", 12, "bold")
        )
        self.lbl_status_texto.pack(side=tk.LEFT)

        # Botão de Auto-Update e Selo de Versão no Topo Direito
        self.btn_atualizar = ttk.Button(
            frame_status,
            text="🔄 Verificar Atualizações",
            command=self._checar_atualizacoes_gui,
        )
        self.btn_atualizar.pack(side=tk.RIGHT)

        self.lbl_versao = tk.Label(
            frame_status,
            text=f"v{self.versao_atual}",
            font=("Arial", 9, "bold"),
            fg="#495057",
            bg="#e9ecef",
            padx=8,
            pady=2,
            relief=tk.GROOVE,
        )
        self.lbl_versao.pack(side=tk.RIGHT, padx=(0, 10))

        # 2. Configurações da Central ERP & Acesso & Alertas WhatsApp
        frame_erp = ttk.LabelFrame(self, text=" ⚙️ Central ERP, Acesso & Notificações ", padding=6)
        frame_erp.pack(fill=tk.X, padx=15, pady=(0, 4))

        # Linha 1: Link do ERP
        frame_url = ttk.Frame(frame_erp)
        frame_url.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(frame_url, text="Link do ERP:", width=15).pack(side=tk.LEFT)
        ent_url = ttk.Entry(frame_url, textvariable=self.var_erp_url, font=("Menlo", 9))
        ent_url.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Linha 2: Usuário, Senha e WhatsApp de Alertas
        frame_cred = ttk.Frame(frame_erp)
        frame_cred.pack(fill=tk.X)

        ttk.Label(frame_cred, text="Usuário ERP:", width=15).pack(side=tk.LEFT)
        ent_user = ttk.Entry(frame_cred, textvariable=self.var_erp_usuario, width=10)
        ent_user.pack(side=tk.LEFT, padx=(0, 8))

        ttk.Label(frame_cred, text="Senha:").pack(side=tk.LEFT, padx=(0, 4))
        self.ent_pass = ttk.Entry(frame_cred, textvariable=self.var_erp_senha, width=10, show="*")
        self.ent_pass.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_ver_senha = ttk.Button(
            frame_cred,
            text="👁",
            width=3,
            command=self._alternar_visibilidade_senha
        )
        self.btn_ver_senha.pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(frame_cred, text="📱 WhatsApp p/ Alertas:").pack(side=tk.LEFT, padx=(0, 4))
        ent_zap_alerta = ttk.Entry(frame_cred, textvariable=self.var_whatsapp_alerta, width=16, font=("Menlo", 9))
        ent_zap_alerta.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(frame_cred, text="(Ex: 5511999998888 ou Nome Contato)", font=("Arial", 8, "italic"), foreground="#666").pack(side=tk.LEFT)

        # Linha 3: Grupos/Chats ignorados na leitura do WhatsApp
        frame_ignorar = ttk.Frame(frame_erp)
        frame_ignorar.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(frame_ignorar, text="🚫 Ignorar Grupos:", width=15).pack(side=tk.LEFT)
        ent_zap_ignorar = ttk.Entry(frame_ignorar, textvariable=self.var_whatsapp_ignorar, font=("Menlo", 9))
        ent_zap_ignorar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        ttk.Label(frame_ignorar, text="(Separe por vírgula. Ex: Grupo Família, Avisos)", font=("Arial", 8, "italic"), foreground="#666").pack(side=tk.LEFT)

        # Salvar automaticamente ao sair de qualquer campo de credencial/configuração
        for widget in (ent_url, ent_user, self.ent_pass, ent_zap_alerta, ent_zap_ignorar):
            widget.bind("<FocusOut>", lambda _e: self._salvar_configuracoes_interface())

        # 3. Controles de Execução Geral & Teste de Nota
        frame_controles = ttk.LabelFrame(self, text=" Controles de Execução & Teste ", padding=6)
        frame_controles.pack(fill=tk.X, padx=15, pady=(0, 4))

        frame_botoes = ttk.Frame(frame_controles)
        frame_botoes.pack(fill=tk.X, pady=(0, 4))

        self.btn_iniciar = tk.Button(
            frame_botoes,
            text="▶ Iniciar Contínuo",
            bg="#28a745",
            fg="white",
            font=("Arial", 10, "bold"),
            padx=8,
            pady=3,
            command=self._iniciar_continuo,
        )
        self.btn_iniciar.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_parar = tk.Button(
            frame_botoes,
            text="⏹ Parar",
            bg="#dc3545",
            fg="white",
            font=("Arial", 10, "bold"),
            padx=8,
            pady=3,
            state=tk.DISABLED,
            command=self._parar,
        )
        self.btn_parar.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_ciclo_unico = ttk.Button(
            frame_botoes,
            text="Ciclo Único",
            command=self._executar_ciclo_unico
        )
        self.btn_ciclo_unico.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_apenas_zap = ttk.Button(
            frame_botoes,
            text="Apenas WhatsApp",
            command=self._executar_apenas_zap
        )
        self.btn_apenas_zap.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_apenas_erp = ttk.Button(
            frame_botoes,
            text="Apenas Lançar ERP",
            command=self._executar_apenas_erp
        )
        self.btn_apenas_erp.pack(side=tk.LEFT, padx=(0, 10))

        btn_zerar = ttk.Button(
            frame_botoes,
            text="🔄 Zerar Checkpoint",
            command=self._zerar_checkpoint,
        )
        btn_zerar.pack(side=tk.RIGHT)

        # Linha 2 de Controles: Opções + Teste Individual de Nota integrado
        frame_opcoes = ttk.Frame(frame_controles)
        frame_opcoes.pack(fill=tk.X)

        chk_teste = ttk.Checkbutton(
            frame_opcoes,
            text="Modo Teste (Navegador Visível)",
            variable=self.var_modo_teste,
            command=self._salvar_configuracoes_interface,
        )
        chk_teste.pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(frame_opcoes, text="Limite:").pack(side=tk.LEFT, padx=(0, 2))
        spn_limite = ttk.Spinbox(frame_opcoes, from_=1, to=100, width=4, textvariable=self.var_limite_notas)
        spn_limite.pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(frame_opcoes, text="Intervalo (s):").pack(side=tk.LEFT, padx=(0, 2))
        spn_intervalo = ttk.Spinbox(frame_opcoes, from_=5, to=300, width=4, textvariable=self.var_intervalo)
        spn_intervalo.pack(side=tk.LEFT, padx=(0, 15))

        # Divisor e Campo de Teste Integrado
        ttk.Separator(frame_opcoes, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        ttk.Label(frame_opcoes, text="🧪 Testar NF:").pack(side=tk.LEFT, padx=(0, 4))
        ent_nota_teste = ttk.Entry(frame_opcoes, textvariable=self.var_nota_teste, width=12, font=("Menlo", 9))
        ent_nota_teste.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_testar_nota = tk.Button(
            frame_opcoes,
            text="🧪 Testar",
            bg="#007bff",
            fg="white",
            font=("Arial", 9, "bold"),
            padx=6,
            pady=1,
            command=self._executar_teste_nota_manual,
        )
        self.btn_testar_nota.pack(side=tk.LEFT)

        # 4. Painel de Métricas e Relatórios (Linha Única com 7 Cartões)
        frame_metricas = ttk.LabelFrame(self, text=" Métricas em Tempo Real & Relatórios ", padding=6)
        frame_metricas.pack(fill=tk.X, padx=15, pady=(0, 4))

        frame_cards = ttk.Frame(frame_metricas)
        frame_cards.pack(fill=tk.X, pady=(0, 4))

        self.card_pendentes = self._criar_card(frame_cards, "Pendentes", "0", "#007bff")
        self.card_lancadas = self._criar_card(frame_cards, "Lançadas", "0", "#28a745", arquivo_relatorio="notas_lancadas.csv")
        self.card_duplicadas = self._criar_card(frame_cards, "Duplicadas", "0", "#fd7e14", arquivo_relatorio="duplicidades.csv")
        self.card_sem_cadastro = self._criar_card(frame_cards, "s/ Cadastro", "0", "#6f42c1", arquivo_relatorio="produtos_sem_cadastro.csv")
        self.card_inexistentes = self._criar_card(frame_cards, "Inexistentes", "0", "#e83e8c", arquivo_relatorio="notas_inexistentes.csv")
        self.card_erros = self._criar_card(frame_cards, "Outros Erros", "0", "#dc3545", arquivo_relatorio="outros_erros.csv")
        self.card_sem_boleto = self._criar_card(frame_cards, "⚠️ s/ Boletos", "0", "#d39e00", arquivo_relatorio="notas_sem_boleto.csv")
        self.card_boletos_div = self._criar_card(frame_cards, "💰 Boletos Div (>R$7)", "0", "#c82333", arquivo_relatorio="boletos_divergentes.csv")

        # Botão de Limpeza Segura de Inconsistências
        frame_acoes_limpeza = ttk.Frame(frame_metricas)
        frame_acoes_limpeza.pack(fill=tk.X, pady=(2, 0))

        btn_limpar_inconsistencias = tk.Button(
            frame_acoes_limpeza,
            text="🧹 Limpar Relatórios de Inconsistências",
            bg="#f8f9fa",
            fg="#dc3545",
            font=("Arial", 8, "bold"),
            relief=tk.GROOVE,
            padx=8,
            pady=2,
            command=self._limpar_inconsistencias,
        )
        btn_limpar_inconsistencias.pack(side=tk.RIGHT)

        ttk.Label(
            frame_acoes_limpeza,
            text="* Preserva notas lançadas e pendentes mesmo com automação rodando.",
            font=("Arial", 8, "italic"),
            foreground="#666"
        ).pack(side=tk.LEFT)
        btn_limpar_inconsistencias.pack(side=tk.RIGHT)

        ttk.Label(
            frame_acoes_limpeza,
            text="* Pode ser acionado a qualquer momento, mesmo com a automação rodando.",
            font=("Arial", 8, "italic"),
            foreground="#666"
        ).pack(side=tk.LEFT)

        # 6. Área Dividida: Fila de Pendentes (Esquerda) e Logs de Execução (Direita)
        frame_paineis = ttk.Frame(self, padding=(15, 6))
        frame_paineis.pack(fill=tk.BOTH, expand=True)

        # Painel da Esquerda: Fila de Pendentes
        frame_fila = ttk.LabelFrame(frame_paineis, text=" 📋 Fila de Notas Pendentes (Apaga ao Lançar) ", padding=8)
        frame_fila.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        lbl_info_fila = ttk.Label(frame_fila, text="Total na fila: 0", font=("Arial", 9, "italic"))
        lbl_info_fila.pack(side=tk.BOTTOM, anchor=tk.W, pady=(6, 2))
        self.lbl_info_fila = lbl_info_fila

        self.list_pendentes = tk.Listbox(
            frame_fila,
            font=("Menlo", 10),
            bg="#ffffff",
            fg="#212529",
            selectbackground="#007bff",
            selectforeground="#ffffff",
            relief=tk.FLAT,
            bd=1,
            height=8,
        )
        scrollbar_fila = ttk.Scrollbar(frame_fila, orient=tk.VERTICAL, command=self.list_pendentes.yview)
        self.list_pendentes.config(yscrollcommand=scrollbar_fila.set)
        scrollbar_fila.pack(side=tk.RIGHT, fill=tk.Y)
        self.list_pendentes.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Painel da Direita: Logs de Execução
        frame_logs = ttk.LabelFrame(frame_paineis, text=" 📜 Terminal de Logs de Execução ", padding=8)
        frame_logs.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        frame_rodape_logs = ttk.Frame(frame_logs)
        frame_rodape_logs.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 2))

        btn_abrir_relatorios = ttk.Button(
            frame_rodape_logs,
            text="📂 Pasta reports/",
            command=self._abrir_pasta_relatorios,
        )
        btn_abrir_relatorios.pack(side=tk.LEFT, padx=(0, 6))

        btn_backup = ttk.Button(
            frame_rodape_logs,
            text="💾 Fazer Backup Agora",
            command=lambda: executar_backup(),
        )
        btn_backup.pack(side=tk.LEFT, padx=(0, 6))

        btn_limpar_logs = ttk.Button(
            frame_rodape_logs,
            text="🧹 Limpar Tela",
            command=lambda: self.txt_logs.delete("1.0", tk.END),
        )
        btn_limpar_logs.pack(side=tk.RIGHT)

        self.txt_logs = scrolledtext.ScrolledText(
            frame_logs,
            wrap=tk.WORD,
            bg="#1e1e1e",
            fg="#dcdcdc",
            insertbackground="white",
            font=("Menlo", 9),
            height=8,
        )
        self.txt_logs.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    def _checar_atualizacoes_gui(self):
        """Verifica se há novas versões no GitHub e aplica a atualização de forma segura."""
        def tarefa():
            try:
                self.btn_atualizar.config(state=tk.DISABLED, text="⏳ Checando...")
                print("[Auto-Updater] Verificando atualizações no GitHub...")
                resultado = verificar_atualizacao()
                self.btn_atualizar.config(state=tk.NORMAL, text="🔄 Verificar Atualizações")

                if resultado.get("disponivel"):
                    nova = resultado.get("nova_versao")
                    atual = resultado.get("versao_atual")
                    desc = resultado.get("descricao")
                    url_zip = resultado.get("url_zip")

                    confirmar = messagebox.askyesno(
                        "Nova Versão Disponível!",
                        f"Uma nova versão foi encontrada no GitHub!\n\n"
                        f"• Versão Atual: v{atual}\n"
                        f"• Nova Versão: v{nova}\n\n"
                        f"Novidades:\n{desc}\n\n"
                        f"Deseja baixar e aplicar a atualização agora?\n"
                        f"(Seus dados, senhas e relatórios serão 100% preservados)"
                    )
                    if confirmar:
                        print(f"[Auto-Updater] Baixando e aplicando a versão v{nova}...")
                        sucesso = baixar_e_aplicar_atualizacao(url_zip)
                        if sucesso:
                            messagebox.showinfo(
                                "Atualização Concluída",
                                f"O sistema foi atualizado com sucesso para a v{nova}!\n\n"
                                f"O aplicativo será reiniciado agora."
                            )
                            reiniciar_aplicacao()
                        else:
                            messagebox.showerror(
                                "Erro na Atualização",
                                "Não foi possível concluir a atualização. Consulte os logs na tela para mais detalhes."
                            )
                else:
                    motivo = resultado.get("motivo", "Você já está utilizando a versão mais recente.")
                    messagebox.showinfo("Verificação de Atualizações", motivo)
            except Exception as e:
                self.btn_atualizar.config(state=tk.NORMAL, text="🔄 Verificar Atualizações")
                print(f"[Auto-Updater] Erro ao verificar atualizações: {e}")
                messagebox.showerror("Erro", f"Falha ao verificar atualizações: {e}")

        threading.Thread(target=tarefa, daemon=True).start()

    def _alternar_visibilidade_senha(self):
        if self.mostrar_senha:
            self.ent_pass.config(show="*")
            self.mostrar_senha = False
        else:
            self.ent_pass.config(show="")
            self.mostrar_senha = True

    def _criar_card(self, pai, titulo, valor_inicial, cor_destaque, arquivo_relatorio=None):
        frame = tk.Frame(pai, relief=tk.GROOVE, bd=1, padx=4, pady=3, bg="#f8f9fa")
        frame.pack(side=tk.LEFT, expand=True, fill=tk.BOTH, padx=2)

        lbl_tit = tk.Label(frame, text=titulo, font=("Arial", 8, "bold"), fg="#495057", bg="#f8f9fa")
        lbl_tit.pack()

        lbl_val = tk.Label(frame, text=valor_inicial, font=("Arial", 15, "bold"), fg=cor_destaque, bg="#f8f9fa")
        lbl_val.pack(pady=(1, 1))

        if arquivo_relatorio:
            btn_ver = tk.Button(
                frame,
                text="Abrir CSV",
                font=("Arial", 7),
                padx=3,
                pady=1,
                command=lambda: self._abrir_arquivo_relatorio(arquivo_relatorio)
            )
            btn_ver.pack()

        return lbl_val

    def _contar_linhas_relatorio(self, nome_arquivo: str) -> int:
        caminho = REPORTS_DIR / nome_arquivo
        if not caminho.exists():
            return 0
        try:
            with open(caminho, "r", encoding="utf-8-sig") as f:
                total = sum(1 for line in f if line.strip()) - 1
                return max(0, total)
        except Exception:
            return 0

    def _limpar_inconsistencias(self):
        confirmar = messagebox.askyesno(
            "Limpar Relatórios de Inconsistências",
            "Deseja realmente limpar todos os relatórios de inconsistências?\n\n"
            "• Serão ZERADOS:\n"
            "   - Duplicidades no ERP\n"
            "   - Produtos sem Cadastro\n"
            "   - Notas Inexistentes\n"
            "   - Outros Erros\n"
            "   - Notas Sem Boletos\n"
            "   - Boletos Divergentes (> R$ 7)\n\n"
            "• Ficarão 100% PRESERVADOS:\n"
            "   - Todas as notas Lançadas com sucesso (notas_lancadas.csv)\n"
            "   - Todas as notas com status Pendente\n\n"
            "Esta ação pode ser feita mesmo com a automação em andamento.\n\n"
            "Deseja prosseguir?"
        )
        if not confirmar:
            return

        arquivos_a_limpar = [
            "duplicidades.csv",
            "produtos_sem_cadastro.csv",
            "notas_inexistentes.csv",
            "outros_erros.csv",
            "notas_sem_boleto.csv",
            "boletos_divergentes.csv",
        ]

        for nome in arquivos_a_limpar:
            caminho = REPORTS_DIR / nome
            if caminho.exists():
                try:
                    caminho.unlink()
                except Exception as e:
                    print(f"[Painel] Aviso ao limpar {nome}: {e}")

        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM notas WHERE status NOT IN ('pendente', 'lancado')")
                conn.commit()
        except Exception as e:
            print(f"[Painel] Aviso ao limpar banco: {e}")

        self._atualizar_metricas()
        print("\n" + "=" * 65)
        print("[Painel] 🧹 Relatórios de inconsistências zerados com sucesso!")
        print("[Painel] ✅ Notas lançadas e pendentes foram 100% preservadas.")
        print("=" * 65 + "\n")
        messagebox.showinfo(
            "Sucesso",
            "Relatórios de inconsistências foram limpos com sucesso!\n\n"
            "Todas as notas Lançadas e Pendentes continuam intactas."
        )

    def _zerar_checkpoint(self):
        confirmar = messagebox.askyesno(
            "Zerar Checkpoint",
            "Deseja realmente zerar o checkpoint do WhatsApp?\n\n"
            "Isso fará com que o robô redefina o marco zero no momento atual na próxima execução, "
            "ignorando todo o histórico anterior (ótimo para quando você levar o projeto para outra máquina)."
        )
        if confirmar:
            zerar_checkpoint_whatsapp()
            messagebox.showinfo(
                "Sucesso",
                "Checkpoint zerado com sucesso!\n"
                "Na próxima vez que o robô iniciar, ele considerará as mensagens a partir deste instante."
            )

    def _abrir_pasta_relatorios(self):
        try:
            caminho = str(REPORTS_DIR.resolve())
            if sys.platform == "darwin":
                subprocess.run(["open", caminho])
            elif sys.platform == "win32":
                os.startfile(caminho)
            else:
                subprocess.run(["xdg-open", caminho])
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível abrir a pasta: {e}")

    def _abrir_arquivo_relatorio(self, nome_arquivo):
        try:
            caminho = REPORTS_DIR / nome_arquivo
            if not caminho.exists():
                messagebox.showinfo("Relatório Vazio", f"Nenhuma ocorrência registrada em '{nome_arquivo}' até o momento.")
                return
            caminho_str = str(caminho.resolve())
            if sys.platform == "darwin":
                subprocess.run(["open", caminho_str])
            elif sys.platform == "win32":
                os.startfile(caminho_str)
            else:
                subprocess.run(["xdg-open", caminho_str])
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível abrir o arquivo: {e}")

    def _definir_estado(self, rodando: bool, mensagem: str = None):
        self.esta_executando = rodando
        if rodando:
            self.lbl_status_circulo.config(text="●", fg="#28a745")
            self.lbl_status_texto.config(text=mensagem or "Executando...")
            self.btn_iniciar.config(state=tk.DISABLED)
            self.btn_parar.config(state=tk.NORMAL)
            self.btn_ciclo_unico.config(state=tk.DISABLED)
            self.btn_apenas_zap.config(state=tk.DISABLED)
            self.btn_apenas_erp.config(state=tk.DISABLED)
            self.btn_testar_nota.config(state=tk.DISABLED)
        else:
            self.lbl_status_circulo.config(text="●", fg="#dc3545")
            self.lbl_status_texto.config(text=mensagem or "Automação Parada")
            self.btn_iniciar.config(state=tk.NORMAL)
            self.btn_parar.config(state=tk.DISABLED)
            self.btn_ciclo_unico.config(state=tk.NORMAL)
            self.btn_apenas_zap.config(state=tk.NORMAL)
            self.btn_apenas_erp.config(state=tk.NORMAL)
            self.btn_testar_nota.config(state=tk.NORMAL)

    def _iniciar_tarefa(self, funcao_alvo, mensagem_status):
        if self.esta_executando:
            return
        self.evento_parar.clear()
        self._definir_estado(True, mensagem_status)

        def worker():
            try:
                funcao_alvo()
            except Exception as e:
                print(f"[Painel - Erro] {e}")
            finally:
                self.after(0, lambda: self._definir_estado(False))
                self.after(0, self._atualizar_metricas)

        self.thread_execucao = threading.Thread(target=worker, daemon=True)
        self.thread_execucao.start()

    def _executar_teste_nota_manual(self):
        numero = self.var_nota_teste.get().strip()
        if not numero:
            messagebox.showwarning("Atenção", "Digite o número da nota que deseja testar!")
            return

        self._salvar_configuracoes_interface()
        url = self.var_erp_url.get().strip()
        user = self.var_erp_usuario.get().strip()
        senha = self.var_erp_senha.get().strip()
        headless = not self.var_modo_teste.get()

        def teste():
            print("\n" + "=" * 65)
            print(f"[Teste Manual] Testando lançamento da nota: {numero}")
            print(f"[Teste Manual] Central: {url} | Usuário: {user}")
            print("=" * 65)

            origem = "Teste_Manual"
            inserir_nota(numero_nota=numero, origem_contato=origem)
            id_unico = gerar_id_unico(origem, numero)

            with ERPLancador(headless=headless, url=url, usuario=user, senha=senha) as erp:
                erp.login_e_navegar_para_entrada()
                resultado = erp.lancar_nota(id_unico=id_unico, numero_nota=numero, origem_contato=origem)
                status = resultado.get("status", "")
                msg = resultado.get("mensagem", "")

                print("=" * 65)
                print(f"[Teste Manual] Concluído! Status: {status.upper()} - {msg}")
                print("=" * 65)

        self._iniciar_tarefa(teste, f"Testando Lançamento da Nota {numero}...")

    def _iniciar_continuo(self):
        self._salvar_configuracoes_interface()
        url = self.var_erp_url.get().strip()
        user = self.var_erp_usuario.get().strip()
        senha = self.var_erp_senha.get().strip()
        limite = self.var_limite_notas.get()
        intervalo = self.var_intervalo.get()
        num_alerta = self.var_whatsapp_alerta.get().strip()
        headless = not self.var_modo_teste.get()

        def loop_continuo():
            print(f"[Painel] Iniciando monitoramento contínuo em tempo real...")
            print(f"[Painel] Central ERP: {url} | Intervalo: {intervalo}s | Navegador: {'Oculto' if headless else 'Visível'}")
            if num_alerta:
                print(f"[Painel] Notificações ativas para o WhatsApp: {num_alerta}")

            with sync_playwright() as p:
                # 1. Abre o WhatsApp uma única vez e mantém ativo na tela
                with WhatsAppMonitor(headless=headless, playwright_instance=p, chats_ignorar=self.var_whatsapp_ignorar.get().strip()) as zap:
                    if not zap.abrir_e_garantir_login():
                        print("[Painel] Não foi possível autenticar o WhatsApp Web. Abortando.")
                        return

                    print("[Painel] WhatsApp Web conectado e pronto na tela.")

                    # 2. Abre o ERP uma única vez, faz login e posiciona no Rec_1020
                    with ERPLancador(headless=headless, url=url, usuario=user, senha=senha, playwright_instance=p) as erp:
                        erp.login_e_navegar_para_entrada()
                        print("[Painel] ERP conectado e posicionado na tela Rec_1020.")
                        print("[Painel] 🚀 Automação pronta! WhatsApp e ERP sincronizados sem recarregar.")

                        while not self.evento_parar.is_set():
                            # Atualiza lista de ignorados caso o usuário tenha alterado na interface
                            zap.chats_ignorar = self.var_whatsapp_ignorar.get().strip()

                            # Auto-recuperação do ERP caso a sessão tenha expirado por inatividade
                            erp.verificar_ou_recuperar_sessao()

                            # Varre novas mensagens no WhatsApp aberto
                            novas_notas, _ = zap.ler_mensagens_e_enfileirar()
                            self._atualizar_metricas()

                            # Processa notas pendentes diretamente no ERP já aberto
                            notas_pendentes = obter_notas_pendentes(limite=limite)
                            for nota in notas_pendentes:
                                if self.evento_parar.is_set():
                                    break

                                num = nota["numero_nota"]
                                origem = nota["origem_contato"]
                                id_unico = nota["id_unico"]

                                res = erp.lancar_nota(id_unico=id_unico, numero_nota=num, origem_contato=origem)
                                st = res.get("status", "")
                                self._atualizar_metricas()

                                # Se lançada com sucesso, reage na hora no WhatsApp já aberto
                                if st == "lancado":
                                    try:
                                        zap.reagir_mensagem_nota_lancada(num, origem_contato=origem)
                                    except Exception as err_reagir:
                                        print(f"[WhatsApp] Aviso ao reagir com ✅: {err_reagir}")

                                # Dispara alerta de inconsistência/boleto se configurado
                                self._enviar_alertas_whatsapp(zap, num_alerta, res, num, origem)

                            for _ in range(intervalo):
                                if self.evento_parar.is_set():
                                    break
                                time.sleep(1)

            print("[Painel] Monitoramento contínuo finalizado.")

        self._iniciar_tarefa(loop_continuo, "Monitoramento Contínuo Ativo")

    def _executar_ciclo_unico(self):
        self._salvar_configuracoes_interface()
        url = self.var_erp_url.get().strip()
        user = self.var_erp_usuario.get().strip()
        senha = self.var_erp_senha.get().strip()
        limite = self.var_limite_notas.get()
        num_alerta = self.var_whatsapp_alerta.get().strip()
        headless = not self.var_modo_teste.get()

        def ciclo():
            print(f"[Painel] Executando ciclo único na Central: {url}...")
            with sync_playwright() as p:
                with WhatsAppMonitor(headless=headless, playwright_instance=p, chats_ignorar=self.var_whatsapp_ignorar.get().strip()) as zap:
                    if not zap.abrir_e_garantir_login():
                        return
                    zap.ler_mensagens_e_enfileirar()
                    self._atualizar_metricas()

                    with ERPLancador(headless=headless, url=url, usuario=user, senha=senha, playwright_instance=p) as erp:
                        erp.login_e_navegar_para_entrada()
                        notas_pendentes = obter_notas_pendentes(limite=limite)
                        for nota in notas_pendentes:
                            res = erp.lancar_nota(nota["id_unico"], nota["numero_nota"], nota["origem_contato"])
                            st = res.get("status", "")
                            self._atualizar_metricas()
                            if st == "lancado":
                                try:
                                    zap.reagir_mensagem_nota_lancada(nota["numero_nota"], origem_contato=nota["origem_contato"])
                                except Exception as e:
                                    print(f"[WhatsApp] Aviso ao reagir com ✅: {e}")
                            self._enviar_alertas_whatsapp(zap, num_alerta, res, nota["numero_nota"], nota["origem_contato"])
            print("[Painel] Ciclo concluído com sucesso.")

        self._iniciar_tarefa(cycle := ciclo, "Executando Ciclo Único...")

    def _executar_apenas_zap(self):
        self._salvar_configuracoes_interface()
        ignorar = self.var_whatsapp_ignorar.get().strip()
        headless = not self.var_modo_teste.get()

        def zap():
            print("[Painel] Verificando novas mensagens no WhatsApp Web...")
            etapa_capturar_whatsapp(chats_ignorar=ignorar, headless=headless)
            print("[Painel] Verificação do WhatsApp concluída.")

        self._iniciar_tarefa(zap, "Lendo WhatsApp...")

    def _executar_apenas_erp(self):
        self._salvar_configuracoes_interface()
        url = self.var_erp_url.get().strip()
        user = self.var_erp_usuario.get().strip()
        senha = self.var_erp_senha.get().strip()
        limite = self.var_limite_notas.get()
        headless = not self.var_modo_teste.get()

        def erp():
            print(f"[Painel] Lançando notas pendentes no ERP ({url})...")
            with ERPLancador(headless=headless, url=url, usuario=user, senha=senha) as erp_inst:
                erp_inst.login_e_navegar_para_entrada()
                notas_pendentes = obter_notas_pendentes(limite=limite)
                if not notas_pendentes:
                    print("[ERP] Nenhuma nota pendente de lançamento na fila.")
                for nota in notas_pendentes:
                    erp_inst.lancar_nota(nota["id_unico"], nota["numero_nota"], nota["origem_contato"])
                    self._atualizar_metricas()
            print("[Painel] Lançamento concluído.")

        self._iniciar_tarefa(erp, "Lançando no ERP...")

    def _parar(self):
        print("[Painel] Solicitando parada da automação... Aguarde o ciclo atual finalizar.")
        self.evento_parar.set()
        self.btn_parar.config(state=tk.DISABLED)

    def _enviar_alertas_whatsapp(self, zap, num_alerta: str, res: dict, num: str, origem: str) -> None:
        """Envia alertas no WhatsApp para inconsistências e notas inexistentes."""
        if not num_alerta:
            return
        st = res.get("status", "")
        try:
            if st == "produto_sem_cadastro":
                zap.enviar_alerta(
                    num_alerta,
                    f"⚠️ *Alerta Lançador de Notas*\nA *NF {num}* possui produtos pendentes de cadastro no ERP!\nOrigem: {origem}",
                )
            elif st == "nota_inexistente":
                zap.enviar_alerta(
                    num_alerta,
                    f"🔎 *Alerta Nota Inexistente*\nA *NF {num}* não existe no ERP (não apareceu no filtro ao digitar)!\nOrigem: {origem}",
                )
            fin = res.get("financeiro", {})
            if fin.get("divergencia"):
                dif = fin.get("diferenca", 0.0)
                zap.enviar_alerta(
                    num_alerta,
                    f"💰 *Alerta Boleto Divergente*\nA *NF {num}* tem boleto R$ {dif:.2f} maior que a nota!\nOrigem: {origem}",
                )
        except Exception as err_alerta:
            print(f"[WhatsApp] Aviso ao enviar alerta: {err_alerta}")

    def _atualizar_metricas(self):
        try:
            stats = obter_resumo_estatisticas()
            self.card_pendentes.config(text=str(stats.get("pendente", 0)))
            self.card_lancadas.config(text=str(stats.get("lancado", 0)))
            self.card_duplicadas.config(text=str(stats.get("duplicada_erp", 0)))
            self.card_sem_cadastro.config(text=str(stats.get("produto_sem_cadastro", 0)))
            self.card_inexistentes.config(text=str(stats.get("nota_inexistente", 0)))
            self.card_erros.config(text=str(stats.get("erro", 0)))

            tot_sem_boleto = self._contar_linhas_relatorio("notas_sem_boleto.csv")
            tot_boletos_div = self._contar_linhas_relatorio("boletos_divergentes.csv")
            self.card_sem_boleto.config(text=str(tot_sem_boleto))
            self.card_boletos_div.config(text=str(tot_boletos_div))

            # Atualizar Lista Visual da Fila de Pendentes
            pendentes = obter_notas_pendentes()
            self.list_pendentes.delete(0, tk.END)
            for nota in pendentes:
                num = nota.get("numero_nota", "")
                origem = nota.get("origem_contato", "WhatsApp")
                self.list_pendentes.insert(tk.END, f"• NF {num}  ({origem})")

            self.lbl_info_fila.config(text=f"Total na fila: {len(pendentes)}")

        except Exception:
            pass
        self.after(5000, self._atualizar_metricas)

    def _processar_fila_logs(self):
        while not self.fila_logs.empty():
            try:
                texto = self.fila_logs.get_nowait()
                self.txt_logs.insert(tk.END, texto)
                self.txt_logs.see(tk.END)
            except queue.Empty:
                break
        self.after(100, self._processar_fila_logs)

    def _salvar_configuracoes_interface(self):
        """Grava no .env as credenciais e parâmetros digitados na interface."""
        try:
            dados = {
                "ERP_URL": self.var_erp_url.get().strip(),
                "ERP_USUARIO": self.var_erp_usuario.get().strip(),
                "ERP_SENHA": self.var_erp_senha.get().strip(),
                "WHATSAPP_ALERTA": self.var_whatsapp_alerta.get().strip(),
                "WHATSAPP_IGNORAR": self.var_whatsapp_ignorar.get().strip(),
                "MODO_TESTE": "true" if self.var_modo_teste.get() else "false",
            }
            try:
                dados["LIMITE_NOTAS_POR_EXECUCAO"] = str(self.var_limite_notas.get())
            except tk.TclError:
                pass
            try:
                dados["INTERVALO_MONITORAMENTO_SEGUNDOS"] = str(self.var_intervalo.get())
            except tk.TclError:
                pass
            salvar_env(dados)
        except Exception as e:
            print(f"[Painel] Aviso ao salvar configurações: {e}")

    def _ao_fechar(self):
        self._salvar_configuracoes_interface()
        if self.esta_executando:
            if messagebox.askyesno("Encerrar", "A automação ainda está rodando. Deseja realmente parar e sair?"):
                self.evento_parar.set()
                executar_backup()
                self.destroy()
        else:
            executar_backup()
            self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
