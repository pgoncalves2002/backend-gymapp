"""
Validators reutilizáveis pelo app workouts.
"""

import os

from django.core.exceptions import ValidationError

# 20 MB — generoso pra GIFs de demonstração de exercício, mas evita upload abusivo.
MAX_DEMO_GIF_BYTES = 20 * 1024 * 1024


def validate_demo_gif_size(file) -> None:
    """Limita o tamanho do GIF demo de exercício."""
    if file.size > MAX_DEMO_GIF_BYTES:
        max_mb = MAX_DEMO_GIF_BYTES // (1024 * 1024)
        raise ValidationError(
            f"Arquivo muito grande ({file.size // (1024 * 1024)} MB). "
            f"Máximo permitido: {max_mb} MB."
        )


def exercise_demo_upload_path(instance, filename: str) -> str:
    """
    Define onde o arquivo é salvo:
        media/exercises/<workout_id>/<exercise_id>.<ext>

    Como Exercise.id é UUID gerado por default antes do save, ele já existe
    quando o upload chega. Renomear pelo `id` evita colisões e dispensa
    sufixos automáticos do Django pra arquivos com o mesmo nome.
    """
    ext = os.path.splitext(filename)[1].lower() or ".gif"
    return f"exercises/{instance.workout_id}/{instance.id}{ext}"
