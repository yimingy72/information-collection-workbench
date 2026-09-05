package gateway

import (
	"encoding/base64"
	"net/http"
	"testing"
	"time"
)

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

func TestCircuitBreakerSkipsFailedEndpoint(t *testing.T) {
	gateway := New()
	endpoints := []string{"wss://bad.example/http", "wss://good.example/http"}
	now := time.Now()
	gateway.markEndpointFailure(endpoints[0], now)
	available := gateway.availableEndpoints(endpoints, 0, now.Add(time.Second))
	if len(available) != 1 || available[0] != endpoints[1] {
		t.Fatalf("available endpoints = %#v, want only %q", available, endpoints[1])
	}
}

func TestCircuitBreakerRestoresEndpointAfterCooldown(t *testing.T) {
	gateway := New()
	endpoint := "wss://bad.example/http"
	now := time.Now()
	gateway.markEndpointFailure(endpoint, now)
	available := gateway.availableEndpoints([]string{endpoint}, 0, now.Add(endpointFailureCooldown+time.Second))
	if len(available) != 1 || available[0] != endpoint {
		t.Fatalf("available endpoints = %#v, want %q after cooldown", available, endpoint)
	}
}


func TestStickyKeySelectsStableEndpoint(t *testing.T) {
	gateway := New()
	endpoints := []string{"wss://one.example/http", "wss://two.example/http", "wss://three.example/http"}
	request, err := http.NewRequest(http.MethodConnect, "https://beian.miit.gov.cn/", nil)
	if err != nil {
		t.Fatal(err)
	}
	request.Header.Set("Proxy-Authorization", "Basic "+base64.StdEncoding.EncodeToString([]byte("lane_0_0:lane_0_0")))
	start := gateway.endpointStart(endpoints, request)
	again := gateway.endpointStart(endpoints, request)
	if start != again {
		t.Fatalf("sticky start changed from %d to %d", start, again)
	}
	if start != 0 {
		t.Fatalf("lane_0_0 should pin to endpoint 0, got %d", start)
	}
	request.Header.Set("Proxy-Authorization", "Basic "+base64.StdEncoding.EncodeToString([]byte("lane_1_4:lane_1_4")))
	if gateway.endpointStart(endpoints, request) != 1 {
		t.Fatal("lane_1_4 should pin to endpoint 1")
	}
	other, err := http.NewRequest(http.MethodConnect, "https://beian.miit.gov.cn/", nil)
	if err != nil {
		t.Fatal(err)
	}
	other.Header.Set("Proxy-Authorization", "Basic "+base64.StdEncoding.EncodeToString([]byte("lane_1_0:lane_1_0")))
	otherStart := gateway.endpointStart(endpoints, other)
	if otherStart < 0 || otherStart >= len(endpoints) {
		t.Fatalf("other lane start %d out of range", otherStart)
	}
}
