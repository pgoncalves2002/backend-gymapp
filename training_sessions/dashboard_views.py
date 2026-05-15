"""
Views do dashboard do trainer — agregações de desempenho dos alunos.

Diferente do `views.py` (que serve o aluno acessando suas próprias sessões),
este módulo é APENAS pra o trainer ver métricas agregadas dos alunos que
ele criou. Os endpoints NÃO expõem ExerciseSetLog cru — só números
resumidos. Isso preserva a privacidade (o trainer vê estatísticas, não
todo dado individual) e blinda performance (agregação ORM).

Endpoints expostos:

    GET /api/trainers/me/dashboard/
        Overview de TODOS os alunos do trainer. Painel da home.

    GET /api/trainers/me/students/{id}/metrics/?range=30d
        Métricas detalhadas de UM aluno. Aba "Desempenho" na StudentPage.

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
from accounts.permissions import IsTrainer
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

        days = _range_days(request)
        now = timezone.now()
        window_start = now - timedelta(days=days)

        # 2. Sessões dentro da janela.
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

        return Response({
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
        })


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
