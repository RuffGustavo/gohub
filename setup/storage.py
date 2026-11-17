from whitenoise.storage import CompressedManifestStaticFilesStorage


class ManifestStaticFilesStorage(CompressedManifestStaticFilesStorage):
    """Storage de estáticos com manifest tolerante a caminhos ausentes.

    O Jazzmin referencia a pasta 'vendor/bootswatch' via {% static %}, que não
    consta do manifest (só arquivos entram nele). Com manifest_strict=False o
    Django devolve a URL sem hash em vez de levantar ValueError (erro 500).
    Efeito colateral: um {% static %} de arquivo inexistente deixa de falhar e
    passa a gerar link quebrado.
    """

    manifest_strict = False
