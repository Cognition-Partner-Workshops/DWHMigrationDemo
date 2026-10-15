from migraai.planning.sections import SECTIONS


def build_prompt(source: str, target: str, attachment_url: str) -> str:
    ordered_sections = "\n".join(f"{section_id}. {title}" for section_id, title in SECTIONS)
    return f"""Write a data-warehouse migration plan for {source} → {target} using ONLY the
attached context and official vendor documentation.
Every factual statement must cite a KB id from the context or an official vendor
documentation URL.
Return exactly the 21 sections below in order and with these titles:
{ordered_sections}
For Q, S, and T set platform_level_estimate to true because object-level discovery has not
happened.
Set manual_review to true wherever evidence is uncertain. Do not open repositories, ask
questions, or wait for input. Return the result only through structured output.
ATTACHMENT:"{attachment_url}"
"""
