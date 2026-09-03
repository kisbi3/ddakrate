from pathlib import Path


APP_JS = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "eligibility"
    / "web"
    / "static"
    / "app.js"
).read_text(encoding="utf-8")


def test_list_uses_only_realizable_values_for_expected_rate_and_interest():
    assert "const expectedRate = item.realizable_rate;" in APP_JS
    assert "const expectedInterest = item.estimated_pre_tax_interest;" in APP_JS
    assert "조건 충족 시 최고" in APP_JS
    assert "conditionalUpperRate ?? item.realizable_rate" not in APP_JS


def test_detail_and_completion_copy_distinguish_pending_state():
    assert "확인 전 조건 포함 최고" in APP_JS
    assert "아직 확인 전인 조건을 모두 충족하면" in APP_JS
    assert "PROVISIONAL_USER_STOPPED" in APP_JS
    assert "PROVISIONAL_DATA_INCOMPLETE" in APP_JS
    assert "현재 추천은 미확인 조건이 남은 잠정 결과" in APP_JS
    assert "실제 상품 내용과 다르거나 최신 정보가 아닐 수 있습니다" in APP_JS


def test_result_list_expands_to_available_viewport_instead_of_stopping_at_ten():
    assert "function expandRecommendationsForViewport(totalCount)" in APP_JS
    assert "Math.ceil(availableHeight / averageHeight) + 1" in APP_JS
    assert "requestAnimationFrame(() => expandRecommendationsForViewport(allItems.length))" in APP_JS


def test_expected_rate_and_institution_history_have_honest_fallbacks():
    assert "실적에 따라 변동" in APP_JS
    assert "계산 전" in APP_JS
    assert "names.map(displayInstitutionName).join(', ')" in APP_JS


def test_card_spend_options_are_amount_specific():
    assert "카드 사용 안 함" in APP_JS
    assert "`월 ${formatWon(amount)}까지`" in APP_JS
