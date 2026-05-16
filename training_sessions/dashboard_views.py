"""
Views do dashboard do trainer — agregações de desempenho dos alunos.

Diferente do `views.py` (que serve o aluno acessando suas próprias sessões),
este módulo é APENAS pra o trainer ver métricas e histórico dos alunos que
ele criou.

Endpoints expostos:

    GET /api/trainers/me/dashboard/
        Overview de TODOS os alunos do trainer. Painel da home.

    GET /api/trainers/me/students/{id}/metrics/?range=30d
        Métricas detalhadas de UM aluno. Aba "Desempenho" na StudentPage.

    GET /api/trainers/me/students/{id}/sessions/?page=1&page_size=20&status=
        Lista paginada do histórico de sessões do aluno (resumo por sessão).
        Aba "Histórico" na StudentPage.

    GET /api/trainers/me/sessions/{session_id}/detail/
        Detalhe de UMA sessão: exercícios da ficha + cada série executada
        (carga real × planejada, reps feitas × target, status). Expandido
        ao clicar em um item da aba "Histórico".

Permission: IsTrainer + scope (só alunos com `created_by=request.user`).
Trainer com `has_full_access` (admin/superuser) vê todos.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.db.models import (
    Avg,
    Count,
    DecimalField,
    F,
    Max,
    Q,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce, TruncWeek
from django.utils import timezone
from rest_framework import permissions
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from accounts.permissions import IsStudent, IsTrainer
from workouts.models import Exercise, WorkoutExercise

from .models import ExerciseSetLog, WorkoutSession


# ---------------------------------------------------------------------------
# Helpers — uma fonte de verdade pra filtros e janelas de tempo.
# ---------------------------------------------------------------------------
def _students_qs(request_user):
    """Alunos visíveis pro trainer logado (ou tudo, pra admin)."""
    base = User.objects.filter(role=User.Role.STUDENT, is_active=True)
    if getattr(request_user, "has_full_access", False):
        return base
    return base.filter(created_by=request_user)


def _range_days(request) -> int:
    """
    `?range=30d|90d|365d|all` → número de dias da janela. `all` = 5 anos
    (limite prático que ainda permite agregação eficiente).
    """
    raw = (request.query_params.get("range") or "30d").lower()
    return {
        "7d": 7, "30d": 30, "90d": 90,
        "180d": 180, "365d": 365,
        "all": 365 * 5,
    }.get(raw, 30)


# ---------------------------------------------------------------------------
# Dashboard — overview de todos os alunos do trainer.
# ---------------------------------------------------------------------------
class TrainerDashboardView(APIView):
    """GET /api/trainers/me/dashboard/

    Retorna painel resumido pro trainer:
      - summary: contadores gerais (alunos, ativos, sessões da semana, etc.)
      - students: lista com mini-status de cada aluno (última sessão,
        frequência últimos 7 dias, dias sem treinar, flag is_active)

    Otimizado: 4 queries no total independente do nº de alunos.
    """

    permission_classes = (permissions.IsAuthenticated, IsTrainer)

    def get(self, request) -> Response:
        students = list(_students_qs(request.user))
        student_ids = [s.id for s in students]

        now = timezone.now()
        week_start = now - timedelta(days=7)
        two_weeks_ago = now - timedelta(days=14)

        # Sessões agrupadas por aluno: total + última + completed + abandoned.
        # Annotate só uma vez, valores por aluno em dict.
        per_student = (
            WorkoutSession.objects
            .filter(student_id__in=student_ids)
            .values("student_id")
            .annotate(
                total_sessions=Count("id"),
                last_session_at=Max("started_at"),
                completed=Count("id", filter=Q(status=WorkoutSession.Status.COMPLETED)),
                abandoned=Count("id", filter=Q(status=WorkoutSession.Status.ABANDONED)),
                sessions_last_7_days=Count(
                    "id", filter=Q(started_at__gte=week_start)
                ),
            )
        )
        per_student_map = {row["student_id"]: row for row in per_student}

        # Última sessão "status" (precisa de subquery — pego o status da sessão
        # com started_at mais recente). Faço numa query separada pra cada aluno
        # com data (alunos sem sessão = nada). Em vez de N queries, uso 1 query
        # ordering e dict.
        last_session_statuses: dict[int, str] = {}
        if student_ids:
            # Pega as sessões ordenadas e mantém só a primeira por aluno.
            for row in (
                WorkoutSession.objects
                .filter(student_id__in=student_ids)
                .order_by("student_id", "-started_at")
                .values("student_id", "status")
            ):
                last_session_statuses.setdefault(row["student_id"], row["status"])

        # Monta o array de students com a info combinada.
        students_payload = []
        for s in students:
            row = per_student_map.get(s.id, {})
            last_at = row.get("last_session_at")
            days_since = (
                int((now - last_at).total_seconds() // 86400)
                if last_at else None
            )
            students_payload.append({
                "student_id": s.id,
                "username": s.username,
                "display_name": s.display_name or s.username,
                "is_active_account": s.is_active,
                "last_session_at": last_at.isoformat() if last_at else None,
                "last_session_status": last_session_statuses.get(s.id),
                "days_since_last_session": days_since,
                "sessions_last_7_days": row.get("sessions_last_7_days", 0),
                "total_sessions": row.get("total_sessions", 0),
                "completed_sessions": row.get("completed", 0),
                "abandoned_sessions": row.get("abandoned", 0),
            })

        # Summary agregado.
        active_this_week = sum(
            1 for s in students_payload
            if s["sessions_last_7_days"] > 0
        )
        inactive_over_2_weeks = sum(
            1 for s in students_payload
            if s["days_since_last_session"] is None
            or s["days_since_last_session"] > 14
        )
        sessions_this_week = sum(
            s["sessions_last_7_days"] for s in students_payload
        )

        # Completion rate global: completed / (completed + abandoned) — ignora
        # in_progress (que ainda não tem desfecho).
        total_completed = sum(s["completed_sessions"] for s in students_payload)
        total_abandoned = sum(s["abandoned_sessions"] for s in students_payload)
        denom = total_completed + total_abandoned
        avg_completion_rate = (
            (total_completed / denom) if denom > 0 else None
        )

        return Response({
            "summary": {
                "total_students": len(students),
                "active_this_week": active_this_week,
                "inactive_over_2_weeks": inactive_over_2_weeks,
                "sessions_this_week": sessions_this_week,
                "avg_completion_rate": (
                    round(avg_completion_rate, 3)
                    if avg_completion_rate is not None else None
                ),
            },
            "students": students_payload,
        })


# ---------------------------------------------------------------------------
# Métricas detalhadas de UM aluno.
# ---------------------------------------------------------------------------
class TrainerStudentMetricsView(APIView):
    """GET /api/trainers/me/students/{student_id}/metrics/?range=30d

    Retorna métricas detalhadas pra a aba "Desempenho" da StudentPage.

    Returns:
      - student: identificação básica
      - range_days: janela usada
      - summary: contadores + médias dentro da janela
      - weekly_frequency: nº de sessões por semana (gráfico de barras)
      - weekly_volume: Σ(load × reps) por semana (gráfico de barras)
      - exercise_progression: histórico de max load por exercício
        (gráfico de linha com seletor de exercício)
      - top_prs: top 5 exercícios por max load (tabela de PRs)
      - recent_sessions: últimas 10 sessões com resumo de sets
    """

    permission_classes = (permissions.IsAuthenticated, IsTrainer)

    def get(self, request, student_id: int) -> Response:
        # 1. Aluno tem que ser visível pro trainer.
        student = _students_qs(request.user).filter(id=student_id).first()
        if student is None:
            raise NotFound("Aluno não encontrado ou não pertence a este trainer.")

        return Response(_build_metrics_payload(student, _range_days(request)))


def _build_metrics_payload(student: User, days: int) -> dict[str, Any]:
    """
    Constrói o dict de métricas pra um aluno numa janela de dias.

    Extraído pra ser reusado em DUAS views:
      - TrainerStudentMetricsView (trainer olhando aluno dele)
      - MyMetricsView              (aluno olhando os próprios dados)

    Lógica é a mesma; difere só na permission e em quem é o `student`.
    """
    now = timezone.now()
    window_start = now - timedelta(days=days)

    sessions_in_window = (
        WorkoutSession.objects
        .filter(student=student, started_at__gte=window_start)
    )

    agg = sessions_in_window.aggregate(
        total=Count("id"),
        completed=Count("id", filter=Q(status=WorkoutSession.Status.COMPLETED)),
        abandoned=Count("id", filter=Q(status=WorkoutSession.Status.ABANDONED)),
        avg_duration=Avg("elapsed_seconds", filter=Q(status=WorkoutSession.Status.COMPLETED)),
    )
    total = agg["total"] or 0
    completed = agg["completed"] or 0
    abandoned = agg["abandoned"] or 0
    denom = completed + abandoned
    completion_rate = (completed / denom) if denom > 0 else None
    avg_duration_min = (
        round((agg["avg_duration"] or 0) / 60.0, 1)
        if agg["avg_duration"] is not None else 0.0
    )

    # 3. Volume total (Σ load_kg × reps_done) das séries concluídas no período.
    volume_sum = (
        ExerciseSetLog.objects
        .filter(
            session__student=student,
            session__started_at__gte=window_start,
            is_completed=True,
        )
        .aggregate(
            total_volume=Coalesce(
                Sum(F("load_kg") * F("reps_done"),
                    output_field=DecimalField(max_digits=12, decimal_places=2)),
                Value(Decimal("0"), output_field=DecimalField(max_digits=12, decimal_places=2)),
            )
        )["total_volume"]
    )

    # 4. Streaks (dias consecutivos com pelo menos 1 sessão completed).
    #    Calculo client-side a partir dos dias com session completed
    #    (1 query pequena).
    completed_dates = sorted(set(
        sessions_in_window
        .filter(status=WorkoutSession.Status.COMPLETED)
        .values_list("started_at__date", flat=True)
    ))
    current_streak, longest_streak = _streaks(completed_dates, today=now.date())

    # 5. Frequência semanal: count de sessões agrupadas por semana ISO.
    weekly_freq_raw = (
        sessions_in_window
        .annotate(week=TruncWeek("started_at"))
        .values("week")
        .annotate(count=Count("id"))
        .order_by("week")
    )
    weekly_frequency = [
        {"week_start": row["week"].date().isoformat(), "sessions": row["count"]}
        for row in weekly_freq_raw
    ]

    # 6. Volume semanal: Σ load×reps das séries completed agrupadas por semana.
    weekly_volume_raw = (
        ExerciseSetLog.objects
        .filter(
            session__student=student,
            session__started_at__gte=window_start,
            is_completed=True,
        )
        .annotate(week=TruncWeek("session__started_at"))
        .values("week")
        .annotate(
            volume=Coalesce(
                Sum(F("load_kg") * F("reps_done"),
                    output_field=DecimalField(max_digits=12, decimal_places=2)),
                Value(Decimal("0"), output_field=DecimalField(max_digits=12, decimal_places=2)),
            )
        )
        .order_by("week")
    )
    weekly_volume = [
        {"week_start": row["week"].date().isoformat(),
         "volume_kg": float(row["volume"])}
        for row in weekly_volume_raw
    ]

    # 7. Evolução por exercício: pra cada exercise, série temporal de
    #    max(load_kg) por semana — usada no gráfico de linha com seletor.
    #    Limito a top 20 exercícios mais executados na janela (evita
    #    response gigante; trainer raramente acompanha >20 exercícios).
    top_exercise_ids = list(
        ExerciseSetLog.objects
        .filter(
            session__student=student,
            session__started_at__gte=window_start,
            is_completed=True,
        )
        .values("workout_exercise__exercise_id")
        .annotate(cnt=Count("id"))
        .order_by("-cnt")
        .values_list("workout_exercise__exercise_id", flat=True)[:20]
    )

    progression_rows = (
        ExerciseSetLog.objects
        .filter(
            session__student=student,
            session__started_at__gte=window_start,
            is_completed=True,
            workout_exercise__exercise_id__in=top_exercise_ids,
        )
        .annotate(week=TruncWeek("session__started_at"))
        .values(
            "workout_exercise__exercise_id",
            "workout_exercise__exercise__name",
            "workout_exercise__exercise__muscle_group",
            "week",
        )
        .annotate(max_load=Max("load_kg"))
        .order_by("workout_exercise__exercise_id", "week")
    )

    progression_by_ex: dict[Any, dict[str, Any]] = {}
    for row in progression_rows:
        ex_id = str(row["workout_exercise__exercise_id"])
        slot = progression_by_ex.setdefault(ex_id, {
            "exercise_id": ex_id,
            "exercise_name": row["workout_exercise__exercise__name"],
            "muscle_group": row["workout_exercise__exercise__muscle_group"],
            "history": [],
            "max_load_kg": 0.0,
        })
        max_load = float(row["max_load"])
        slot["history"].append({
            "week_start": row["week"].date().isoformat(),
            "max_load_kg": max_load,
        })
        if max_load > slot["max_load_kg"]:
            slot["max_load_kg"] = max_load

    exercise_progression = list(progression_by_ex.values())

    # 8. Top PRs (max load por exercício na janela), ordenado por carga.
    prs = sorted(
        (
            {
                "exercise_id": e["exercise_id"],
                "exercise_name": e["exercise_name"],
                "muscle_group": e["muscle_group"],
                "max_load_kg": e["max_load_kg"],
            }
            for e in exercise_progression
        ),
        key=lambda x: x["max_load_kg"],
        reverse=True,
    )[:5]

    # 9. Recent sessions: últimas 10 com count de sets completed/total.
    recent_qs = (
        WorkoutSession.objects
        .filter(student=student)
        .order_by("-started_at")[:10]
        .annotate(
            sets_total=Count("set_logs"),
            sets_completed=Count("set_logs", filter=Q(set_logs__is_completed=True)),
        )
        .select_related("workout")
    )
    recent_sessions = [
        {
            "id": str(s.id),
            "workout_id": str(s.workout_id),
            "workout_name": s.workout.name,
            "workout_focus": s.workout.focus,
            "started_at": s.started_at.isoformat(),
            "finished_at": s.finished_at.isoformat() if s.finished_at else None,
            "elapsed_minutes": round(s.elapsed_seconds / 60.0, 1) if s.elapsed_seconds else 0,
            "status": s.status,
            "sets_total": s.sets_total,
            "sets_completed": s.sets_completed,
        }
        for s in recent_qs
    ]

    return {
        "student": {
            "id": student.id,
            "username": student.username,
            "display_name": student.display_name or student.username,
        },
        "range_days": days,
        "summary": {
            "total_sessions": total,
            "completed_sessions": completed,
            "abandoned_sessions": abandoned,
            "completion_rate": (
                round(completion_rate, 3) if completion_rate is not None else None
            ),
            "avg_session_duration_minutes": avg_duration_min,
            "total_volume_kg": float(volume_sum) if volume_sum is not None else 0.0,
            "current_streak_days": current_streak,
            "longest_streak_days": longest_streak,
        },
        "weekly_frequency": weekly_frequency,
        "weekly_volume": weekly_volume,
        "exercise_progression": exercise_progression,
        "top_prs": prs,
        "recent_sessions": recent_sessions,
    }


# ---------------------------------------------------------------------------
# Histórico de sessões (lista paginada).
# ---------------------------------------------------------------------------
class TrainerStudentSessionsView(APIView):
    """GET /api/trainers/me/students/{student_id}/sessions/
       ?page=1&page_size=20&status=completed|abandoned|in_progress

    Lista paginada das sessões do aluno (mais recentes primeiro). Resumo
    por sessão — sem set_logs aqui. Pra ver as séries de uma sessão use
    /trainers/me/sessions/{id}/detail/.

    Por que não usar DRF PageNumberPagination com o ViewSet existente?
      O ViewSet de sessions usa IsSessionOwner (bloqueia trainer). Aqui
      precisamos do scoping invertido: trainer pode ver sessions DOS
      alunos dele. Endpoint dedicado isola essa lógica.
    """

    permission_classes = (permissions.IsAuthenticated, IsTrainer)

    DEFAULT_PAGE_SIZE = 20
    MAX_PAGE_SIZE = 100

    def get(self, request, student_id: int) -> Response:
        student = _students_qs(request.user).filter(id=student_id).first()
        if student is None:
            raise NotFound("Aluno não encontrado ou não pertence a este trainer.")
        return Response(_build_sessions_page_payload(student, request))


_DEFAULT_PAGE_SIZE = 20
_MAX_PAGE_SIZE = 100


def _build_sessions_page_payload(student: User, request) -> dict[str, Any]:
    """
    Constrói a página de sessões pra um aluno. Reusado por:
      - TrainerStudentSessionsView (trainer olhando aluno dele)
      - MySessionsView              (aluno olhando o próprio histórico)

    Lê `page`, `page_size`, `status` dos query params da request.
    """
    # Parsing seguro — defaults razoáveis se vier lixo.
    try:
        page = max(1, int(request.query_params.get("page", "1")))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(request.query_params.get("page_size", _DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        page_size = _DEFAULT_PAGE_SIZE
    page_size = max(1, min(page_size, _MAX_PAGE_SIZE))

    qs = (
        WorkoutSession.objects
        .filter(student=student)
        .select_related("workout")
        .annotate(
            sets_total=Count("set_logs"),
            sets_completed=Count("set_logs", filter=Q(set_logs__is_completed=True)),
        )
        .order_by("-started_at")
    )

    status_filter = (request.query_params.get("status") or "").lower()
    valid_statuses = {c.value for c in WorkoutSession.Status}
    if status_filter in valid_statuses:
        qs = qs.filter(status=status_filter)

    total = qs.count()
    start = (page - 1) * page_size
    end = start + page_size
    page_items = list(qs[start:end])

    results = [
        {
            "id": str(s.id),
            "workout_id": str(s.workout_id),
            "workout_name": s.workout.name,
            "workout_focus": s.workout.focus,
            "workout_day_label": s.workout.day_label,
            "started_at": s.started_at.isoformat(),
            "finished_at": s.finished_at.isoformat() if s.finished_at else None,
            "elapsed_minutes": (
                round(s.elapsed_seconds / 60.0, 1) if s.elapsed_seconds else 0
            ),
            "status": s.status,
            "sets_total": s.sets_total,
            "sets_completed": s.sets_completed,
        }
        for s in page_items
    ]

    return {
        "count": total,
        "page": page,
        "page_size": page_size,
        "has_next": end < total,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Detalhe de UMA sessão — exercícios + cada série logada.
# ---------------------------------------------------------------------------
class TrainerSessionDetailView(APIView):
    """GET /api/trainers/me/sessions/{session_id}/detail/

    Retorna a sessão "esmiuçada": pra cada WorkoutExercise da ficha
    executada, lista as séries (set_logs) com carga real × planejada,
    reps feitas × target, status.

    Útil pro trainer revisar o que o aluno de fato fez (não só agregação).

    Acesso: o aluno da sessão tem que pertencer ao trainer logado.
    """

    permission_classes = (permissions.IsAuthenticated, IsTrainer)

    def get(self, request, session_id) -> Response:
        # Scope via student.created_by — sem isso, qualquer trainer com
        # UUID adivinhado leria sessões de outros. Filter na cadeia já cobre.
        session = (
            WorkoutSession.objects
            .filter(student__in=_students_qs(request.user))
            .select_related("workout", "student")
            .filter(id=session_id)
            .first()
        )
        if session is None:
            raise NotFound("Sessão não encontrada ou aluno não pertence ao trainer.")
        return Response(_build_session_detail_payload(session))


# ===========================================================================
# Endpoints "self" — aluno consultando os PRÓPRIOS dados.
# Mesma lógica das views do trainer, mas:
#   - Permission: IsStudent (em vez de IsTrainer)
#   - student = request.user (em vez de lookup por id)
#   - Sessão pode ser de qualquer aluno → checagem é student == request.user
# ===========================================================================
class MyMetricsView(APIView):
    """GET /api/students/me/metrics/?range=30d

    Versão "self" do TrainerStudentMetricsView. Aluno olha as próprias
    métricas. Não recebe student_id — deduz do JWT.
    """

    permission_classes = (permissions.IsAuthenticated, IsStudent)

    def get(self, request) -> Response:
        return Response(_build_metrics_payload(request.user, _range_days(request)))


class MySessionsView(APIView):
    """GET /api/students/me/sessions/?page=1&page_size=20&status=

    Histórico de sessões do PRÓPRIO aluno. Mesmo payload da view do trainer.
    """

    permission_classes = (permissions.IsAuthenticated, IsStudent)

    def get(self, request) -> Response:
        return Response(_build_sessions_page_payload(request.user, request))


class MySessionDetailView(APIView):
    """GET /api/students/me/sessions/{session_id}/detail/

    Detalhe de UMA sessão do PRÓPRIO aluno. Reusa o serializer/payload da
    TrainerSessionDetailView mas com scope `session.student == request.user`.

    404 se a sessão pertence a outro aluno — protege contra um aluno
    chutando UUIDs e lendo sessão alheia.
    """

    permission_classes = (permissions.IsAuthenticated, IsStudent)

    def get(self, request, session_id) -> Response:
        session = (
            WorkoutSession.objects
            .filter(student=request.user, id=session_id)
            .select_related("workout", "student")
            .first()
        )
        if session is None:
            raise NotFound("Sessão não encontrada.")

        # Compor o mesmo payload da TrainerSessionDetailView — em vez de
        # duplicar, instancia ela e chama internamente. O método `get()` faz
        # o lookup de novo (com scope diferente), mas é barato e mantém
        # consistência se o formato mudar lá.
        # ALTERNATIVA escolhida: replicar o mínimo necessário inline aqui
        # pra não acoplar a duas permission classes. A construção do payload
        # é a parte cara; o scope é a parte segura.
        return Response(_build_session_detail_payload(session))


def _build_session_detail_payload(session: WorkoutSession) -> dict[str, Any]:
    """
    Payload de detalhe de uma sessão — exercícios da ficha + séries logadas.
    Extraído pra reuso entre TrainerSessionDetailView e MySessionDetailView.
    """
    workout_exercises = (
        WorkoutExercise.objects
        .filter(workout=session.workout)
        .select_related("exercise")
        .order_by("order")
    )

    logs_by_we: dict[Any, list[ExerciseSetLog]] = defaultdict(list)
    for log in (
        ExerciseSetLog.objects
        .filter(session=session)
        .order_by("workout_exercise__order", "set_number")
    ):
        logs_by_we[log.workout_exercise_id].append(log)

    exercises_payload = []
    for we in workout_exercises:
        logs = logs_by_we.get(we.id, [])

        def planned_load_for_set(set_number: int):
            idx = set_number - 1
            if isinstance(we.set_loads, list) and 0 <= idx < len(we.set_loads):
                v = we.set_loads[idx]
                if v is not None:
                    return float(v)
            return float(we.load_kg) if we.load_kg is not None else None

        sets_payload = [
            {
                "set_number": log.set_number,
                "load_kg": float(log.load_kg),
                "reps_done": log.reps_done,
                "is_completed": log.is_completed,
                "completed_at": (
                    log.completed_at.isoformat() if log.completed_at else None
                ),
                "target_load_kg": planned_load_for_set(log.set_number),
                "target_reps": we.reps,
            }
            for log in logs
        ]

        exercises_payload.append({
            "workout_exercise_id": str(we.id),
            "exercise_id": str(we.exercise_id),
            "exercise_name": we.exercise.name,
            "muscle_group": we.exercise.muscle_group,
            "order": we.order,
            "group_id": str(we.group_id) if we.group_id else None,
            "sets_planned": we.sets,
            "reps_planned": we.reps,
            "rest_seconds": we.rest_seconds,
            "technique_note": we.effective_technique_note,
            "sets": sets_payload,
        })

    all_logs = [l for logs in logs_by_we.values() for l in logs]
    total_sets = len(all_logs)
    completed_sets = sum(1 for l in all_logs if l.is_completed)
    total_volume = sum(
        float(l.load_kg) * l.reps_done
        for l in all_logs if l.is_completed
    )

    return {
        "session": {
            "id": str(session.id),
            "workout_id": str(session.workout_id),
            "workout_name": session.workout.name,
            "workout_focus": session.workout.focus,
            "workout_day_label": session.workout.day_label,
            "student_id": session.student_id,
            "student_display_name": (
                session.student.display_name or session.student.username
            ),
            "started_at": session.started_at.isoformat(),
            "finished_at": (
                session.finished_at.isoformat() if session.finished_at else None
            ),
            "elapsed_minutes": (
                round(session.elapsed_seconds / 60.0, 1)
                if session.elapsed_seconds else 0
            ),
            "status": session.status,
            "sets_total": total_sets,
            "sets_completed": completed_sets,
            "total_volume_kg": round(total_volume, 2),
        },
        "exercises": exercises_payload,
    }


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------
def _streaks(dates: list, today) -> tuple[int, int]:
    """
    Calcula (current_streak, longest_streak) a partir de uma lista ordenada
    de datas com pelo menos 1 sessão concluída.

    - current_streak: dias consecutivos contando de hoje pra trás. Se hoje
      não tem sessão, considera "ontem" como base (streak não quebra no
      próprio dia). Se mais de 1 dia sem treino, streak = 0.
    - longest_streak: maior sequência consecutiva no histórico.

    Garbage in, garbage out: assume `dates` ordenada ascendente.
    """
    if not dates:
        return 0, 0

    # longest
    longest = 1
    current = 1
    for prev, curr in zip(dates, dates[1:]):
        if (curr - prev).days == 1:
            current += 1
            longest = max(longest, current)
        elif (curr - prev).days == 0:
            continue  # mesma data — não conta dupla
        else:
            current = 1

    # current_streak: começa do último dia e volta
    last = dates[-1]
    gap = (today - last).days
    if gap > 1:
        return 0, longest  # quebrou
    # streak atual = sequência contínua terminando em `last`
    streak = 1
    for i in range(len(dates) - 2, -1, -1):
        if (dates[i + 1] - dates[i]).days == 1:
            streak += 1
        elif (dates[i + 1] - dates[i]).days == 0:
            continue
        else:
            break
    return streak, max(longest, streak)
