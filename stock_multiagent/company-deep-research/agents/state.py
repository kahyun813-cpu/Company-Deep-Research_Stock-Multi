from typing_extensions import TypedDict

class ResearchState(TypedDict, total=False):
    # 입력
    run_id:          str
    ticker:          str
    company_name:    str
    report_date:     str
    sector:          str
    sector_avg_pe:   float
    current_price:   float
    competitor_tickers: list

    # 각 노드 출력 (순서대로 채워짐)
    researcher_output:  dict
    checklist_output:   dict
    report_output:      dict

    # Lazy fetching / caching (ephemeral within a single run)
    raw_collected_data:        dict
    data_cache:                dict
    insufficient_item_ids:     list[str]
    data_categories_requested: list[str]
    research_loop_count:       int   # reflect 루프 반복 횟수 (0-indexed, reflect_node에서 증가)

    # Sectional drafting intermediates
    section_outputs:          list[dict]

    # 에러 추적
    errors: list