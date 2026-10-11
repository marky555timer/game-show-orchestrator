# drivers/token_tracker.py
"""Session-wide Anthropic API token accumulator, shared by every AI call
site (OCR title cleanup in rekordbox_driver.py, trivia/Price Game prompts
in factoid_engine.py) and read by the gamepad Btn3-hold overlay
(graphics/matrix_canvas.py). Deliberately a leaf module -- no imports from
state/config -- so both AI call sites can import it with zero circular-
import risk."""
import threading

_lock = threading.Lock()
_input_tokens = 0
_output_tokens = 0


_other = {}   # model -> [input, output]; non-Haiku usage (question audits)


def record_usage(input_tokens, output_tokens, model=None):
    """`model` None = the Haiku runtime calls the Btn3 overlay prices. Any
    other model (Sonnet/Opus question audits) is tallied separately so it
    can't be mis-priced at Haiku rates."""
    global _input_tokens, _output_tokens
    if not input_tokens and not output_tokens:
        return
    with _lock:
        if model:
            t = _other.setdefault(model, [0, 0])
            t[0] += int(input_tokens or 0)
            t[1] += int(output_tokens or 0)
            return
        _input_tokens += int(input_tokens or 0)
        _output_tokens += int(output_tokens or 0)


def get_other():
    """{model: (input_tokens, output_tokens)} for non-Haiku calls."""
    with _lock:
        return {m: tuple(v) for m, v in _other.items()}


def get_totals():
    """Returns (input_tokens, output_tokens, total) for the whole session."""
    with _lock:
        return _input_tokens, _output_tokens, _input_tokens + _output_tokens
