"""
Este arquivo é apenas histórico (1ª iteração da modelagem, na fase 1).

Os models reais foram implementados nos apps:
    - accounts/models.py          (User custom estendendo AbstractUser)
    - workouts/models.py          (Workout, Exercise)
    - training_sessions/models.py (WorkoutSession, ExerciseSetLog)

Diferença em relação a este preview: a entidade Profile foi absorvida pelo
User custom (AbstractUser), seguindo a recomendação oficial do Django pra
projetos novos. Mais detalhes em MODELAGEM.md, seção 2.1.
"""
