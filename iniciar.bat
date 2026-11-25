@echo off
title Lancador de Notas Fiscais - Painel de Controle
chcp 65001 >nul

echo ========================================================
echo    Iniciando o Painel de Controle do Lancador de Notas   
echo ========================================================
echo.

:: Verifica se o Python esta acessivel
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERRO] O Python nao foi encontrado no sistema!
    echo Certifique-se de que o Python esta instalado e com a opcao:
    echo 'Add python.exe to PATH' marcada no instalador.
    echo.
    pause
    exit /b 1
)

:: Executa a interface grafica
python app.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo [AVISO] A aplicacao foi encerrada com erro.
    pause
)
