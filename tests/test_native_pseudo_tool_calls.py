from bauer.agent_tool_protocol import normalize_native_pseudo_call


AVAILABLE = {"app_factory_init", "read_file"}


def test_recovers_observed_function_json_envelope():
    text = 'Função\n\njson\n{"name":"app_factory_init","arguments":{"idea":"fase 11","path":"fase-11"}}'
    call = normalize_native_pseudo_call(text, AVAILABLE)
    assert call is not None
    assert call["function"]["name"] == "app_factory_init"
    assert call["function"]["arguments"] == '{"idea": "fase 11", "path": "fase-11"}'


def test_recovers_openai_nested_function_envelope():
    call = normalize_native_pseudo_call(
        '```json\n{"id":"call-1","function":{"name":"read_file","arguments":"{\\"path\\":\\"README.md\\"}"}}\n```',
        AVAILABLE,
    )
    assert call is not None
    assert call["id"] == "call-1"
    assert call["function"]["name"] == "read_file"


def test_rejects_unknown_tool_and_invalid_arguments():
    assert normalize_native_pseudo_call('{"name":"shell","arguments":{}}', AVAILABLE) is None
    assert normalize_native_pseudo_call('{"name":"read_file","arguments":"[]"}', AVAILABLE) is None


def test_ordinary_text_is_not_a_tool_call():
    assert normalize_native_pseudo_call("A fase 11 deve começar agora.", AVAILABLE) is None
