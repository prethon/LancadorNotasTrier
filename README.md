# Lançador de Notas Fiscais (WhatsApp Web -> ERP)

Projeto em Python com **Playwright** e **SQLite** para monitoramento automático de números de notas fiscais recebidas via WhatsApp Web e lançamento automatizado em sistema ERP web legado, com controle de fila anti-duplicidade, relatórios automáticos de inconsistências e painel visual de controle.

---

## 📁 Estrutura do Projeto

```text
LancadorNotas/
├── app.py                        # Interface Gráfica Desktop (Painel de Controle)
├── main.py                       # Orquestrador via linha de comando
├── data/
│   ├── notas.db                  # Banco de dados local SQLite (gerado automaticamente)
│   └── whatsapp_checkpoint.json  # Ponto de partida para ler apenas novas mensagens
├── reports/                      # Relatórios gerados automaticamente em CSV (Excel)
│   ├── duplicidades.csv          # Notas já lançadas no ERP
│   ├── produtos_sem_cadastro.csv # Notas que barraram por falta de cadastro de produto
│   └── outros_erros.csv          # Outras exceções ou falhas técnicas
├── sessions/                     # Perfis persistentes de navegador (.gitignore)
│   ├── whatsapp/                 # Sessão salva do WhatsApp Web (não pede QR Code todo dia)
│   └── erp/                      # Sessão salva do ERP
├── src/
│   ├── config.py                 # Configurações gerais e caminhos relativos portáteis
│   ├── database.py               # Persistência SQLite, anti-duplicação e transições de status
│   ├── extractor.py              # Extração e normalização de números de notas via regex
│   ├── reporter.py               # Geração dos relatórios separados em formato CSV
│   ├── whatsapp.py               # Monitoramento e leitura do WhatsApp Web
│   └── erp.py                    # Automação de lançamento no ERP
├── .env                          # Variáveis de ambiente reais (não versionado)
├── .env.example                  # Modelo de variáveis de ambiente
├── requirements.txt              # Dependências do projeto
└── README.md                     # Documentação
```

---

## 🚀 Como Configurar em Qualquer Máquina

### 1. Pré-requisitos
- Python 3.9 ou superior instalado.
- Acesso à internet para instalação dos pacotes.

### 2. Instalação das Dependências

Abra o terminal na pasta do projeto e execute:

```bash
# Instalar bibliotecas Python
python3 -m pip install -r requirements.txt

# Instalar o navegador Chromium utilizado pelo Playwright
python3 -m playwright install chromium
```

### 3. Configuração do `.env`

Copie o arquivo de exemplo ou edite o `.env` existente:

```bash
cp .env.example .env
```

Abra o arquivo `.env` e configure:

```env
# Modo seguro: abre o navegador na sua tela (headless=False)
MODO_TESTE=true

# Limite de notas por execução durante testes
LIMITE_NOTAS_POR_EXECUCAO=1

# Configurações do ERP
ERP_URL=http://rcfarma2.ddns.net:4647/sgfpod1/Login.pod
ERP_USUARIO=2
ERP_SENHA=d19m04

# WhatsApp Web (deixe vazio para usar o chat ativo na tela)
WHATSAPP_CHAT_ALVO=
```

---

## 🖥️ Como Executar

### 🌟 Opção 1: Pela Interface Gráfica Desktop (Recomendado)

Para abrir a janela do Painel de Controle com botões visuais, métricas em tempo real e visualizador de logs:

```bash
python3 app.py
```

**Recursos da Interface:**
- **▶ Iniciar Monitoramento Contínuo** e **⏹ Parar**: Controle instantâneo em segundo plano sem travar a tela.
- **Ações Rápidas**: Botões para *Executar Ciclo Único*, *Apenas Ler WhatsApp* ou *Apenas Lançar ERP*.
- **Métricas Vivas**: Contadores de notas pendentes, lançadas, duplicidades e produtos sem cadastro.
- **Acesso com 1 Clique aos Relatórios**: Botões para abrir as planilhas de erros diretamente no Excel e abrir a pasta `reports/` no Finder.
- **Terminal de Logs Integrado**: Acompanhe cada etapa da automação em tempo real.

---

### 💻 Opção 2: Pelo Terminal / Linha de Comando

Se preferir rodar diretamente via terminal:

#### 1. Ciclo Único Completo
```bash
python3 main.py
```

#### 2. Monitoramento Contínuo
Roda em loop checando novas mensagens no intervalo configurado:
```bash
python3 main.py --continuo
```

#### 3. Apenas Ler WhatsApp
```bash
python3 main.py --apenas-whatsapp
```

#### 4. Apenas Lançar ERP
```bash
python3 main.py --apenas-erp --limite 5
```

---

## 📱 Regras de Captura do WhatsApp Web

1. **Apenas Novas Mensagens:** Na primeira inicialização, o robô grava a última mensagem visível como marco zero em `data/whatsapp_checkpoint.json`. Todas as mensagens históricas anteriores são ignoradas e apenas mensagens enviadas a partir desse momento são lidas.
2. **Conversão Direta:** Como o WhatsApp é dedicado ao envio de notas, qualquer mensagem de texto recebida é tratada como número de nota fiscal (suportando uma nota por mensagem ou múltiplas notas separadas por linha).

---

## 📊 Relatórios Automáticos de Inconsistências

Sempre que uma nota não puder ser lançada no ERP, o robô atualiza o banco de dados e registra uma linha formatada em arquivos CSV separados dentro da pasta `reports/`:

- `reports/duplicidades.csv`: Notas que já constavam como lançadas no sistema.
- `reports/produtos_sem_cadastro.csv`: Notas bloqueadas por terem produtos sem cadastro no ERP.
- `reports/outros_erros.csv`: Notas não localizadas na consulta ou erros inesperados.

Os relatórios utilizam delimitador ponto e vírgula (`;`) e codificação `UTF-8-SIG`, abrindo com as colunas perfeitamente formatadas no Excel.
