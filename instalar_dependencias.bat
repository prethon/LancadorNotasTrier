@echo off
title Instalador de Dependencias - Lancador de Notas
chcp 65001 >nul

echo ========================================================
echo      INSTALADOR DE DEPENDÊNCIAS DO LANÇADOR DE NOTAS     
echo ========================================================
echo.

:: 1. Verifica se o Python esta acessivel
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERRO] O Python nao foi encontrado no Windows!
    echo.
    echo Para corrigir:
    echo 1. Acesse https://www.python.org/downloads/ e instale o Python.
    echo 2. IMPORTANTE: Na primeira tela do instalador, MARQUE a opcao:
    echo    "Add python.exe to PATH"
    echo 3. Apos concluir a instalacao, execute este arquivo novamente.
    echo.
    pause
    exit /b 1
)

echo [1/3] Verificando e atualizando o instalador de pacotes pip...
python -m pip install --upgrade pip
echo.

echo [2/3] Instalando as bibliotecas do projeto (Playwright e Dotenv)...
python -m pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERRO] Falha ao instalar as dependencias do requirements.txt.
    pause
    exit /b 1
)
echo.

echo [3/3] Baixando os navegadores necessarios (Playwright Chromium)...
python -m playwright install chromium
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERRO] Falha ao baixar os binarios do navegador Chromium.
    pause
    exit /b 1
)
echo.

echo ========================================================
echo   INSTALACAO CONCLUIDA COM SUCESSO!
echo.
echo   Tudo pronto para uso.
echo   Agora basta dar dois cliques no arquivo 'iniciar.bat'
echo   para abrir o Painel de Controle da automacao!
echo ========================================================
echo.
pause
