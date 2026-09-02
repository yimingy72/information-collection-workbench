package gateway

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"path"
	"strings"
	"sync/atomic"
	"time"

	"asset-workbench/seamoon-core/internal/tunnel"
	"github.com/gorilla/websocket"
)

type Config struct {
	Enabled            bool     `json:"enabled"`
	Endpoint           string   `json:"endpoint"` // legacy first endpoint
	Endpoints          []string `json:"endpoints,omitempty"`
	InsecureSkipVerify bool     `json:"insecure_skip_verify"`
}

type Gateway struct {
	config atomic.Value
	cursor atomic.Uint64
}

func New() *Gateway {
	gateway := &Gateway{}
	gateway.config.Store(Config{})
	return gateway
}

func (g *Gateway) Current() Config {
	return g.config.Load().(Config)
}

func (g *Gateway) Update(next Config) error {
	rawEndpoints := append([]string(nil), next.Endpoints...)
	if len(rawEndpoints) == 0 && strings.TrimSpace(next.Endpoint) != "" {
		rawEndpoints = []string{next.Endpoint}
	}
	normalized := make([]string, 0, len(rawEndpoints))
	seen := make(map[string]struct{}, len(rawEndpoints))
	for _, raw := range rawEndpoints {
		value := strings.TrimSpace(raw)
		if value == "" {
			continue
		}
		endpoint, err := normalizeEndpoint(value)
		if err != nil {
			return err
		}
		if _, ok := seen[endpoint]; ok {
			continue
		}
		seen[endpoint] = struct{}{}
		normalized = append(normalized, endpoint)
	}
	if next.Enabled && len(normalized) == 0 {
		return errors.New("at least one cloud function endpoint is required")
	}
	next.Endpoints = normalized
	if len(normalized) > 0 {
		next.Endpoint = normalized[0]
	} else {
		next.Endpoint = strings.TrimSpace(next.Endpoint)
	}
	g.config.Store(next)
	return nil
}

func normalizeEndpoint(raw string) (string, error) {
	value := strings.TrimSpace(raw)
	if value == "" {
		return "", errors.New("cloud function endpoint is required")
	}
	parsed, err := url.Parse(value)
	if err != nil {
		return "", fmt.Errorf("invalid cloud function endpoint: %w", err)
	}
	switch strings.ToLower(parsed.Scheme) {
	case "https":
		parsed.Scheme = "wss"
	case "http":
		parsed.Scheme = "ws"
	case "wss", "ws":
	default:
		return "", errors.New("cloud function endpoint must use http, https, ws, or wss")
	}
	if parsed.Host == "" {
		return "", errors.New("cloud function endpoint has no host")
	}
	cleanPath := strings.TrimSuffix(parsed.Path, "/")
	if cleanPath == "" {
		parsed.Path = "/http"
	} else if !strings.HasSuffix(cleanPath, "/http") {
		parsed.Path = path.Join(cleanPath, "http")
	}
	return parsed.String(), nil
}

func (g *Gateway) ServeProxy(ctx context.Context, address string) error {
	listener, err := net.Listen("tcp", address)
	if err != nil {
		return err
	}
	defer listener.Close()
	go func() {
		<-ctx.Done()
		_ = listener.Close()
	}()
	log.Printf("SeaMoon gateway proxy listening on %s", address)
	for {
		client, err := listener.Accept()
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			log.Printf("SeaMoon gateway accept failed: %v", err)
			continue
		}
		go g.handle(client)
	}
}

func (g *Gateway) handle(client net.Conn) {
	defer client.Close()
	config := g.Current()
	if !config.Enabled || len(config.Endpoints) == 0 {
		_, _ = io.WriteString(client, "HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 37\r\n\r\nSeaMoon cloud function is not enabled")
		return
	}
	dialer := websocket.Dialer{
		HandshakeTimeout: 15 * time.Second,
		ReadBufferSize:   32 * 1024,
		WriteBufferSize:  32 * 1024,
		TLSClientConfig: &tls.Config{ // #nosec G402 -- explicit user-controlled compatibility option.
			MinVersion:         tls.VersionTLS12,
			InsecureSkipVerify: config.InsecureSkipVerify,
		},
	}
	start := int(g.cursor.Add(1)-1) % len(config.Endpoints)
	for attempt := range config.Endpoints {
		endpoint := config.Endpoints[(start+attempt)%len(config.Endpoints)]
		ws, response, err := dialer.Dial(endpoint, nil)
		if err == nil {
			remote := tunnel.Wrap(ws)
			defer remote.Close()
			relay(client, remote)
			return
		}
		status := ""
		if response != nil {
			status = response.Status
		}
		log.Printf("SeaMoon gateway dial failed endpoint=%s status=%s err=%v", endpoint, status, err)
	}
	_, _ = io.WriteString(client, "HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 34\r\n\r\nSeaMoon cloud function unavailable")
}

func relay(left, right net.Conn) {
	done := make(chan struct{}, 2)
	copyConn := func(dst, src net.Conn) {
		_, _ = io.Copy(dst, src)
		_ = dst.Close()
		_ = src.Close()
		done <- struct{}{}
	}
	go copyConn(left, right)
	go copyConn(right, left)
	<-done
}

func (g *Gateway) ServeAdmin(ctx context.Context, address string) error {
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(writer).Encode(map[string]any{"status": "ok", "config": g.Current()})
	})
	mux.HandleFunc("/config", func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPut && request.Method != http.MethodPost {
			writer.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		defer request.Body.Close()
		var config Config
		decoder := json.NewDecoder(http.MaxBytesReader(writer, request.Body, 64*1024))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&config); err != nil {
			http.Error(writer, err.Error(), http.StatusBadRequest)
			return
		}
		if err := g.Update(config); err != nil {
			http.Error(writer, err.Error(), http.StatusBadRequest)
			return
		}
		writer.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(writer).Encode(map[string]any{"status": "ok", "config": g.Current()})
	})
	server := &http.Server{Addr: address, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdownCtx)
	}()
	log.Printf("SeaMoon gateway admin listening on %s", address)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}
