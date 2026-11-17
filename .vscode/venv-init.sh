# Carrega a configuração normal do bash e depois ativa o .venv do projeto
[ -f ~/.bashrc ] && source ~/.bashrc
# Mantém a shell integration do VS Code (o --init-file desativa a injeção automática)
if [ "$TERM_PROGRAM" = "vscode" ] && command -v code >/dev/null; then
    source "$(code --locate-shell-integration-path bash)" 2>/dev/null
fi
[ -f "$VENV_PROJECT_DIR/.venv/bin/activate" ] && source "$VENV_PROJECT_DIR/.venv/bin/activate"
