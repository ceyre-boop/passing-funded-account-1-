def pytest_configure(config):
    config.addinivalue_line("markers", "network: needs live market data")
