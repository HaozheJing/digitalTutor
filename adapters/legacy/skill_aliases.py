"""Historical Skill names and knowledge-route hints; no extra business capabilities."""
WEEKLY_INTERACTION_SKILL = 'invite-reading-interaction'
SKILL_ALIASES = dict.fromkeys((
    'share-reading-feelings', 'quiz-reading-knowledge',
    'practice-reading-method', 'connect-reading-to-life'), WEEKLY_INTERACTION_SKILL)

KNOWLEDGE_SKILL_ROUTES = {
    'answer-course-service-question':['answer-faq'],
    'explain-lesson':['explain-reading-content'],
    'follow-up-unanswered-question':['escalate-unanswered-question'],
    'handle-complaint-escalation':['handle-refund-complaint'],
    'plan-makeup-learning':['supervise-learning'],
    'address-learning-concern':['address-learning-concern'],
}
