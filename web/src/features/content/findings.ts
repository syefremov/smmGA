export const findingLabels: Record<string, string> = {
  human_claims_review_required:
    "Человек должен проверить смысл утверждений и их соответствие источникам.",
  knowledge_gap: "Остался незакрытый пробел в данных.",
  unverified_or_stale_evidence:
    "Источник не подтверждён, устарел или заменён новой версией.",
  evidence_unavailable: "Связанный источник недоступен.",
  brief_expired: "Срок актуальности брифа истёк.",
  product_facts_required: "Для продуктового брифа нужны подтверждённые факты.",
  fact_product_mismatch: "Факт относится к другому продукту.",
  brand_profile_required: "Нужны подтверждённые правила бренда.",
  claim_policy_required: "Нужны подтверждённые правила утверждений.",
  claim_rule_match: "Найдена формулировка из правил утверждений.",
  disclaimer_missing: "Не хватает обязательной оговорки.",
  pilot_format_limit:
    "Превышен внутренний лимит пилота: 4000 символов или 4 вложения.",
  unsafe_link: "В тексте обнаружена потенциально небезопасная ссылка.",
  media_changed: "Метаданные вложения изменились после создания редакции.",
  media_type_not_supported: "Тип вложения пока не поддерживается.",
  media_bytes_require_manual_check:
    "Проверьте сам файл и права на него вручную; сервер проверил только метаданные.",
  media_unavailable: "Вложение недоступно.",
  revision_integrity_error: "Нарушена целостность редакции.",
};
