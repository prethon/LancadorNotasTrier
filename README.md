# 🚀 Lançador de Notas Fiscais (WhatsApp Web ➔ ERP)

Sistema inteligente e portátil desenvolvido em **Python**, **Playwright** e **SQLite** para automação completa do ciclo de captura de números de notas fiscais via WhatsApp Web, validação anti-duplicidade, lançamento automatizado em sistema ERP Web legado, auditoria financeira de boletos e painel de controle com **Auto-Updater integrado**.

---

## ⚡ Instalação Rápida no Windows (Apenas 2 Cliques)

Não é necessário digitar nenhum comando no terminal para instalar ou rodar no Windows:

### 1. Pré-requisito (Apenas uma vez por máquina)
1. Baixe e instale o Python (3.10 ou superior) em [python.org/downloads](https://www.python.org/downloads/).
2. ⚠️ **MUITO IMPORTANTE:** Na primeira tela do instalador, marque a caixa:
   > ☑️ **Add python.exe to PATH**

### 2. Instalação das Dependências (1 Clique)
- Dê dois cliques no arquivo: 👉 **`instalar_dependencias.bat`**
- O instalador atualizará o `pip`, instalará as bibliotecas necessárias e baixará automaticamente o navegador Chromium do robô.

### 3. Iniciar o Sistema (1 Clique)
- Dê dois cliques no arquivo: 👉 **`iniciar.bat`**
- O **Painel de Controle** abrirá imediatamente!

---

## 🍏 Instalação no macOS / Linux

Abra o Terminal na pasta do projeto e execute:

```bash
# 1. Instalar bibliotecas Python
python3 -m pip install -r requirements.txt

# 2. Baixar o navegador do robô
python3 -m playwright install chromium

# 3. Executar o Painel de Controle
python3 app.py
```

---

## 🎛️ Recursos do Painel de Controle Desktop

A interface gráfica foi desenhada para operação contínua e simples:

- **▶ Iniciar Monitoramento Contínuo / ⏹ Parar:** Controle da automação em segundo plano sem travar o computador.
- **📋 Fila Visual de Notas Pendentes:** Painel lateral dedicado que lista as notas recebidas e as **remove automaticamente em tempo real** conforme são lançadas.
- **🔄 Auto-Updater em 1 Clique:** Botão `[ 🔄 Verificar Atualizações ]` no topo da tela conectado ao GitHub para atualizar o software em segundos sem perder dados locais.
- **📱 Alertas no WhatsApp:** Campo para cadastrar um número/contato que receberá avisos automáticos caso uma nota tenha produtos sem cadastro ou boletos com divergência.
- **✅ Reação Automática no WhatsApp:** O robô reage com o emoji `✅` na mensagem original da nota **apenas e exclusivamente** quando o lançamento for concluído com sucesso no ERP.
- **🧪 Teste Individual de Lançamento:** Campo para digitar qualquer número de nota e validar o fluxo completo no ERP na hora.
- **🧹 Limpeza Segura de Inconsistências:** Botão que zera relatórios de erros a qualquer momento, mantendo **100% intactas** as notas lançadas e pendentes.
- **💾 Backup Automático (.zip):** Gera arquivos de cópia de segurança na pasta `backups/`, mantendo os últimos 15 backups em rotação.

---

## 📊 Auditoria e Relatórios Automáticos (`reports/`)

O sistema classifica cada ocorrência e gera relatórios separados em formato **CSV (Excel)** com delimitador `;` e codificação `UTF-8-SIG`:

| Arquivo de Relatório | Descrição da Ocorrência |
| :--- | :--- |
| `reports/notas_lancadas.csv` | Notas fiscais importadas e lançadas com sucesso no ERP. |
| `reports/duplicidades.csv` | Notas que já constavam na aba de *Importadas* do ERP. |
| `reports/produtos_sem_cadastro.csv` | Notas barradas por conter itens que não constam no cadastro de produtos. |
| `reports/notas_sem_boleto.csv` | Notas lançadas no ERP que ficaram sem duplicatas/boletos gerados no Contas a Pagar (`Cad_0160`). |
| `reports/boletos_divergentes.csv` | Notas onde o valor do boleto é maior que o valor da nota além da tolerância de **R$ 7,00**. |
| `reports/outros_erros.csv` | Notas não encontradas na consulta ou falhas técnicas de conexão. |

---

## 🔄 Como Funciona o Auto-Updater via GitHub

O software possui atualização automática nativa:
- Quando o usuário clica em **`[ 🔄 Verificar Atualizações ]`**, o sistema consulta o arquivo `version.json` no repositório configurado no GitHub.
- Se houver uma nova versão, o sistema exibe a lista de novidades e pergunta se deseja atualizar.
- Ao confirmar, o robô:
  1. Cria um backup automático prévio de segurança.
  2. Baixa a nova versão do código.
  3. 🛡️ **Preserva 100%:** O arquivo `.env` (senhas), o banco `data/notas.db`, os relatórios `reports/` e a sessão do WhatsApp `sessions/` **NUNCA são sobrescritos**.
  4. Reinicia o aplicativo já na nova versão!

---

## 📦 Como Gerar Pacotes Limpos para Clientes

Se você for distribuir o sistema para outras filiais ou pessoas, **nunca copie a sua pasta inteira** (para não vazar suas senhas ou seu WhatsApp logado).

Para gerar uma versão limpa para clientes:
1. No Windows, dê dois cliques em: 👉 **`gerar_pacote_cliente.bat`** *(ou rode `python3 gerar_pacote_cliente.py` no Mac)*.
2. O script criará o arquivo **`LancadorNotas_Versao_Cliente.zip`**.
3. Esse arquivo compactado já vem:
   - ✅ Sem senhas (usa modelo `.env` limpo);
   - ✅ Sem histórico de notas antigas e sem relatórios;
   - ✅ Sem sessão do WhatsApp (o cliente escaneia o dele);
   - ✅ Pronto para extrair, instalar e usar.

---

## 📁 Estrutura de Diretórios do Projeto

```text
LancadorNotas/
├── app.py                        # Interface Gráfica Desktop
├── main.py                       # Orquestrador via linha de comando
├── version.json                  # Informações da versão atual instalada
├── requirements.txt              # Bibliotecas necessárias (Playwright, Dotenv)
├── iniciar.bat                   # Atalho para abrir no Windows (2 cliques)
├── instalar_dependencias.bat     # Atalho para instalar dependências no Windows (2 cliques)
├── gerar_pacote_cliente.bat      # Atalho para empacotar versão limpa no Windows
├── gerar_pacote_cliente.py       # Script gerador de pacote seguro para clientes
├── data/                         # Banco SQLite e checkpoint (ignorado no git)
│   ├── notas.db
│   └── whatsapp_checkpoint.json
├── reports/                      # Planilhas CSV geradas automaticamente
├── backups/                      # Cópias de segurança em .zip (rotativo, últimos 15)
├── sessions/                     # Perfis persistentes de navegador (WhatsApp e ERP)
├── src/
│   ├── config.py                 # Resolução de caminhos relativos e leitura do .env
│   ├── database.py               # SQLite, anti-duplicação e controle de fila
│   ├── extractor.py              # Extração e higienização dos números das notas
│   ├── reporter.py               # Registro formatado nos relatórios CSV
│   ├── whatsapp.py               # Automação do WhatsApp Web (leitura, reações e alertas)
│   ├── erp.py                    # Automação do ERP (login, navegação, importação e Cad_0160)
│   ├── backup.py                 # Gerador de backups compactados em .zip
│   └── updater.py                # Sistema de auto-update seguro via GitHub
├── .env.example                  # Modelo de variáveis de ambiente
└── README.md                     # Esta documentação
```

---

## ⚙️ Configurações Opcionais (`.env`)

Você pode editar o arquivo `.env` com suas configurações preferenciais:

```env
# Modo seguro: se true, exibe a janela do navegador em tempo real
MODO_TESTE=true

# Limite de notas por execução em modo de teste
LIMITE_NOTAS_POR_EXECUCAO=1

# Configurações do ERP Web
ERP_URL=http://rcfarma2.ddns.net:4647/sgfpod1/Login.pod
ERP_USUARIO=2
ERP_SENHA=sua_senha_aqui

# Repositório GitHub para Auto-Updater
GITHUB_REPO=prethon/LancadorNotasTrier

# WhatsApp Web (deixe vazio para usar a conversa aberta)
WHATSAPP_CHAT_ALVO=
```
