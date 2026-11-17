from scripts.maintain_nas_routes import rule_subnet


def test_rule_subnet_supports_iproute_json_formats():
    assert rule_subnet({'dst': '172.25.0.0', 'dstlen': 16}) == '172.25.0.0/16'
    assert rule_subnet({'dst': '172.25.0.0/16'}) == '172.25.0.0/16'
    assert rule_subnet({'dst': '192.168.5.6'}) == '192.168.5.6/32'
    assert rule_subnet({'table': 'main'}) is None
