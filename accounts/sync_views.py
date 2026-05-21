"""
Endpoint /api/sync/ — devolve em UMA request tudo que o app precisa
pra ficar funcional offline (user + fichas + workout_exercises com o
catálogo Exercise expandido em cada item).

Otimizado com prefetch — sem N+1 ao iterar.
"""

from django.db.models import Prefetch
from django.utils import timezone
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from workouts.models import Workout, WorkoutExercise
from workouts.serializers import WorkoutDetailSerializer

from .serializers import UserSerializer


class SyncView(APIView):
    """
    GET /api/sync/

    Resposta:
    {
        "user": {...},
        "workouts": [
            {
                ...,
                "workout_exercises": [
                    {
                        "id": ...,
                        "order": 0, "sets": 4, "reps": "8-12",
                        "load_kg": "40.00", "rest_seconds": 90,
                        "exercise_detail": {
                            "id": ..., "name": "Supino reto",
                            "muscle_group": "Peitoral",
                            "demo_gif": "https://.../media/exercises/.../x.gif",
                            ...
                        }
                    },
                    ...
                ]
            },
            ...
        ],
        "synced_at": "2026-05-05T14:30:00Z"
    }

    O cliente baixa cada `exercise_detail.demo_gif` em paralelo e armazena
    como Data no SwiftData (LocalExerciseTemplate.demoGifData).
    """

    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        user = request.user

        qs = (
            Workout.objects
            .select_related("student", "trainer")
            .prefetch_related(
                Prefetch(
                    "workout_exercises",
                    queryset=(
                        WorkoutExercise.objects
                        .select_related("exercise", "exercise__created_by")
                        .order_by("order")
                    ),
                )
            )
        )
        if user.is_trainer:
            qs = qs.filter(trainer=user)
            workouts_data = WorkoutDetailSerializer(
                qs, many=True, context={"request": request}
            ).data
        else:
            # Validade do ACESSO do aluno: se o trainer setou data limite e
            # ela já passou (ou pagamento atrasou no caso de aluno com
            # pagamento interno), zera as fichas. Aluno continua logando OK
            # mas vê tela "Acesso expirado" no app.
            if not user.is_within_validity:
                return Response({
                    "user": UserSerializer(user).data,
                    "workouts": [],
                    "synced_at": timezone.now().isoformat(),
                })
            # Aluno NUNCA recebe fichas arquivadas no sync — elas só ficam
            # visíveis pro trainer no SPA quando ele toggla "Ver arquivadas".
            qs = qs.filter(student=user, is_archived=False)
            # Janela de validade da FICHA (independente da validade do aluno):
            #   - valid_from null OU <= hoje (não é "futura")
            #   - valid_until null OU >= hoje (não é "expirada")
            # Permite ao personal programar fichas futuras sem confundir o
            # aluno até a data programada.
            from django.db.models import Q
            from datetime import date
            today = date.today()
            qs = qs.filter(
                Q(valid_from__isnull=True) | Q(valid_from__lte=today)
            ).filter(
                Q(valid_until__isnull=True) | Q(valid_until__gte=today)
            )
            workouts_data = WorkoutDetailSerializer(
                qs, many=True, context={"request": request}
            ).data

        return Response({
            "user": UserSerializer(user).data,
            "workouts": workouts_data,
            "synced_at": timezone.now().isoformat(),
        })
