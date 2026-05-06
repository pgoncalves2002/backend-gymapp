"""
Endpoint /api/sync/ — devolve em UMA request tudo que o app precisa
pra ficar funcional offline (user + fichas + exercícios).

Otimizado pra 1 query de Workout + 1 query de Exercise (prefetch_related),
em vez de N+1 que o cliente faria batendo em /api/workouts/ e depois em
/api/exercises/?workout=X pra cada ficha.
"""

from django.utils import timezone
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from workouts.models import Exercise, Workout
from workouts.serializers import WorkoutDetailSerializer

from .serializers import UserSerializer


class SyncView(APIView):
    """
    GET /api/sync/

    Resposta:
    {
        "user": {...},
        "workouts": [{...with exercises:[]...}, ...],
        "synced_at": "2026-05-05T14:30:00Z"
    }

    O cliente persiste tudo localmente (SwiftData) e baixa cada `demo_gif`
    em paralelo a partir das URLs presentes nos exercises.
    """

    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        user = request.user

        # Mesma lógica de visibilidade do WorkoutViewSet:
        # aluno vê suas fichas; trainer vê as que criou.
        qs = (
            Workout.objects
            .select_related("student", "trainer")
            .prefetch_related(
                "exercises",  # ordenação default já é por `order` (Meta.ordering)
            )
        )
        if user.is_trainer:
            qs = qs.filter(trainer=user)
        else:
            qs = qs.filter(student=user)

        workouts_data = WorkoutDetailSerializer(
            qs, many=True, context={"request": request}
        ).data

        return Response({
            "user": UserSerializer(user).data,
            "workouts": workouts_data,
            "synced_at": timezone.now().isoformat(),
        })
