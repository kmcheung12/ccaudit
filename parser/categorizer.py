# parser/categorizer.py (temporary stub — will be replaced in Task 4)
from parser.models import CategoryBreakdown

def categorize(text: str, input_tokens: int, system_prompt_tokens: int = 0) -> CategoryBreakdown:
    bd = CategoryBreakdown()
    bd.messages_tokens = input_tokens
    return bd
