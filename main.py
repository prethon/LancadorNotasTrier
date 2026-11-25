import sys
import time
import argparse
from pathlib import Path
from src.config import (
    MODO_TESTE,
    LIMITE_NOTAS_POR_EXECUCAO,
    INTERVALO_MONITORAMENTO_SEGUNDOS,
)
from src.database import (
    init_db,
    obter_notas_pendentes,
    obter_resumo_estatisticas,
)
from src.whatsapp import WhatsAppMonitor, CHECKPOINT_FILE
from src.erp import ERPLancador
from src.backup import executar_backup


def zerar_checkpoint_whatsapp() -> bool:
    """Apaga o arquivo de checkpoint para forçar uma nova marcação de marco zero."""
    if CHECKPOINT_FILE.exists():
        try:
            CHECKPOINT_FILE.unlink()
            print("[WhatsApp] Checkpoint zerado com sucesso! Na próxima execução, o histórico antigo será redefinido.")
            return True
        except Exception as e:
            print(f"[WhatsApp] Erro ao zerar checkpoint: {e}")
            return False
    else:
        print("[WhatsApp] Nenhum checkpoint encontrado para zerar.")
        return True


def exibir_banner() -> None:
    print("=" * 65)
    print("      SISTEMA DE CAPTURA E LANÇAMENTO DE NOTAS FISCAIS       ")
    print("=" * 65)
    print(f" Modo de Teste Seguro: {'ATIVADO (headless=False)' if MODO_TESTE else 'DESATIVADO'}")
    print(f" Limite de Lançamentos por Ciclo: {LIMITE_NOTAS_POR_EXECUCAO}")
    print("=" * 65)


def exibir_estatisticas() -> None:
    stats = obter_resumo_estatisticas()
    print("\n" + "-" * 40)
    print(" RESUMO ATUAL DO BANCO DE DADOS LOCAL:")
    print("-" * 40)
    print(f"  • Pendentes:            {stats.get('pendente', 0)}")
    print(f"  • Lançadas com Sucesso: {stats.get('lancado', 0)}")
    print(f"  • Duplicidades no ERP:  {stats.get('duplicada_erp', 0)} (em reports/duplicidades.csv)")
    print(f"  • Produtos s/ Cadastro: {stats.get('produto_sem_cadastro', 0)} (em reports/produtos_sem_cadastro.csv)")
    print(f"  • Erros / Outros:       {stats.get('erro', 0)} (em reports/outros_erros.csv)")
    print("-" * 40 + "\n")


def etapa_capturar_whatsapp() -> int:
    """Executa a leitura das mensagens do WhatsApp Web e enfileira novas notas."""
    print("\n>>> ETAPA 1: Captura de Notas no WhatsApp Web")
    with WhatsAppMonitor() as monitor:
        sucesso = monitor.abrir_e_garantir_login()
        if not sucesso:
            print("[WhatsApp] Não foi possível autenticar a sessão do WhatsApp. Pulando etapa.")
            return 0
        total_novas, _ = monitor.ler_mensagens_e_enfileirar()
        return total_novas


def etapa_lancar_erp(
    limite: int = None,
    url: str = None,
    usuario: str = None,
    senha: str = None,
    zap_monitor: WhatsAppMonitor = None,
    numero_alerta: str = None,
) -> int:
    """
    Executa o lançamento no ERP para notas 'pendente'.
    Se zap_monitor estiver ativo:
      - Reage com ✅ nas notas lançadas com sucesso.
      - Dispara alerta no WhatsApp se houver produto sem cadastro ou divergência financeira.
    """
    print("\n>>> ETAPA 2: Lançamento de Notas no ERP Web")
    notas_pendentes = obter_notas_pendentes(limite=limite)

    if not notas_pendentes:
        print("[ERP] Nenhuma nota pendente de lançamento na fila.")
        return 0

    print(f"[ERP] {len(notas_pendentes)} nota(s) pendente(s) selecionada(s) para lançamento.")

    total_processadas = 0
    with ERPLancador(url=url, usuario=usuario, senha=senha) as erp:
        erp.login_e_navegar_para_entrada()

        for nota in notas_pendentes:
            id_unico = nota["id_unico"]
            numero_nota = nota["numero_nota"]
            origem = nota["origem_contato"]

            resultado = erp.lancar_nota(id_unico, numero_nota, origem)
            status = resultado.get("status", "")
            total_processadas += 1

            # 1. Reação ✅ no WhatsApp apenas para notas lançadas
            if status == "lancado" and zap_monitor:
                try:
                    zap_monitor.reagir_mensagem_nota_lancada(numero_nota, origem_contato=origem)
                except Exception as e:
                    print(f"[WhatsApp] Aviso ao reagir com emoji: {e}")

            # 2. Alerta imediato no WhatsApp se configurado
            if numero_alerta and zap_monitor:
                try:
                    if status == "produto_sem_cadastro":
                        msg_alerta = (
                            f"⚠️ *Alerta Lançador de Notas*\n"
                            f"A *NF {numero_nota}* possui produtos pendentes de cadastro no ERP e não pôde ser importada!\n"
                            f"Origem: {origem}"
                        )
                        zap_monitor.enviar_alerta(numero_alerta, msg_alerta)

                    fin = resultado.get("financeiro", {})
                    if fin.get("divergencia"):
                        dif = fin.get("diferenca", 0.0)
                        msg_alerta = (
                            f"💰 *Alerta de Boleto Divergente*\n"
                            f"A *NF {numero_nota}* foi lançada, mas o boleto é R$ {dif:.2f} maior que a nota (acima da tolerância de R$ 7,00)!\n"
                            f"Origem: {origem}"
                        )
                        zap_monitor.enviar_alerta(numero_alerta, msg_alerta)
                except Exception as e:
                    print(f"[WhatsApp] Aviso ao enviar mensagem de alerta: {e}")

            if MODO_TESTE:
                time.sleep(2)

    return total_processadas


def ciclo_completo(limite_lancamento: int = None, url: str = None, usuario: str = None, senha: str = None) -> None:
    """Executa o ciclo padrão: Captura WhatsApp -> Lançamento ERP -> Estatísticas -> Backup."""
    etapa_capturar_whatsapp()
    etapa_lancar_erp(limite=limite_lancamento, url=url, usuario=usuario, senha=senha)
    exibir_estatisticas()
    executar_backup()


def main():
    init_db()

    parser = argparse.ArgumentParser(description="Automação de Captura e Lançamento de NF-e.")
    parser.add_argument("--apenas-whatsapp", action="store_true", help="Executa apenas a leitura do WhatsApp.")
    parser.add_argument("--apenas-erp", action="store_true", help="Executa apenas o lançamento no ERP.")
    parser.add_argument("--continuo", action="store_true", help="Executa em loop contínuo.")
    parser.add_argument("--limite", type=int, default=LIMITE_NOTAS_POR_EXECUCAO, help="Limite de notas por ciclo.")
    parser.add_argument("--erp-url", type=str, default=None, help="URL da Central ERP.")
    parser.add_argument("--erp-usuario", type=str, default=None, help="Usuário do ERP.")
    parser.add_argument("--erp-senha", type=str, default=None, help="Senha do ERP.")
    parser.add_argument("--zerar-checkpoint", action="store_true", help="Zera o checkpoint do WhatsApp.")

    args = parser.parse_args()

    if args.zerar_checkpoint:
        zerar_checkpoint_whatsapp()
        return

    exibir_banner()

    if args.apenas_whatsapp:
        etapa_capturar_whatsapp()
        exibir_estatisticas()
        return

    if args.apenas_erp:
        etapa_lancar_erp(limite=args.limite, url=args.erp_url, usuario=args.erp_usuario, senha=args.erp_senha)
        exibir_estatisticas()
        return

    if args.continuo:
        print(f"[Sistema] Modo Contínuo ativado. Intervalo: {INTERVALO_MONITORAMENTO_SEGUNDOS}s.")
        try:
            while True:
                ciclo_completo(limite_lancamento=args.limite, url=args.erp_url, usuario=args.erp_usuario, senha=args.erp_senha)
                print(f"[Sistema] Aguardando {INTERVALO_MONITORAMENTO_SEGUNDOS}s até a próxima checagem...")
                time.sleep(INTERVALO_MONITORAMENTO_SEGUNDOS)
        except KeyboardInterrupt:
            print("\n[Sistema] Execução interrompida.")
        return

    ciclo_completo(limite_lancamento=args.limite, url=args.erp_url, usuario=args.erp_usuario, senha=args.erp_senha)


if __name__ == "__main__":
    main()
