"""
Permissões customizadas reusadas pelos ViewSets dos outros apps.

Convenções:
    - `IsTrainer` / `IsStudent`: liberam o endpoint inteiro com base no role.
    - `IsWorkoutOwnerOrTrainer`: object-level — verifica se o usuário tem
      relação com aquele objeto específico (aluno dono ou trainer criador).
"""

from rest_framework.permissions import BasePermission, SAFE_METHODS


class IsTrainer(BasePermission):
    """Permite apenas usuários com role=trainer."""

    message = "Apenas personal trainers podem executar essa ação."

    def has_permission(self, request, view) -> bool:
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_trainer
        )


class IsStudent(BasePermission):
    """Permite apenas usuários com role=student."""

    message = "Apenas alunos podem executar essa ação."

    def has_permission(self, request, view) -> bool:
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_student
        )


class IsWorkoutOwnerOrTrainer(BasePermission):
    """
    Object-level: o aluno dono ou o trainer que criou a ficha podem ler/editar.
    Quem não tem relação não enxerga o objeto.

    Use junto com `IsAuthenticated` no `permission_classes`.
    """

    def has_object_permission(self, request, view, obj) -> bool:
        # `obj` pode ser um Workout ou algo que tenha `.workout`
        workout = getattr(obj, "workout", obj)
        user = request.user

        is_owner = workout.student_id == user.id
        is_creator = workout.trainer_id == user.id

        if request.method in SAFE_METHODS:
            # GET/HEAD/OPTIONS: aluno dono OU trainer criador
            return is_owner or is_creator

        # Métodos de escrita: só o trainer criador edita.
        # Aluno pode ler a ficha mas não modificá-la (a edição é do personal).
        return is_creator


class IsSessionOwner(BasePermission):
    """
    Object-level pra WorkoutSession e ExerciseSetLog: só o aluno dono da
    sessão pode acessá-la (o trainer criou a ficha, mas a *execução* é do aluno).
    """

    def has_object_permission(self, request, view, obj) -> bool:
        session = getattr(obj, "session", obj)
        return session.student_id == request.user.id
