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
        else:
            # Aluno NUNCA recebe fichas arquivadas no sync — elas só ficam
            # visíveis pro trainer no SPA quando ele toggla "Ver arquivadas".
            qs = qs.filter(student=user, is_archived=False)

        workouts_data = WorkoutDetailSerializer(
            qs, many=True, context={"request": request}
        ).data

        return Response({
            "user": UserSerializer(user).data,
            "workouts": workouts_data,
            "synced_at": timezone.now().isoformat(),
        })
