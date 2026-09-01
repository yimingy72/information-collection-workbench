package gateway

import "testing"

func TestNormalizeEndpoint(t *testing.T) {
	tests := map[string]string{
		"https://example.test":         "wss://example.test/http",
		"http://example.test/base/":    "ws://example.test/base/http",
		"wss://example.test/base/http": "wss://example.test/base/http",
	}
	for input, expected := range tests {
		actual, err := normalizeEndpoint(input)
		if err != nil {
			t.Fatalf("normalizeEndpoint(%q): %v", input, err)
		}
		if actual != expected {
			t.Fatalf("normalizeEndpoint(%q) = %q, want %q", input, actual, expected)
		}
	}
}

func TestNormalizeEndpointRejectsUnsupportedScheme(t *testing.T) {
	if _, err := normalizeEndpoint("socks5://example.test:1080"); err == nil {
		t.Fatal("expected unsupported scheme error")
	}
}
