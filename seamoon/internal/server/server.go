package server

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"strings"
	"time"

	"asset-workbench/seamoon-core/internal/tunnel"
	"github.com/gorilla/websocket"
)

func Serve(ctx context.Context, address string) error {
	upgrader := websocket.Upgrader{
		HandshakeTimeout: 15 * time.Second,
		ReadBufferSize:   32 * 1024,
		WriteBufferSize:  32 * 1024,
		CheckOrigin:      func(*http.Request) bool { return true },
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/_health", func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("Content-Type", "text/plain; charset=utf-8")
		_, _ = io.WriteString(writer, "OK\nasset-workbench-seamoon-core\n")
	})
	mux.HandleFunc("/http", func(writer http.ResponseWriter, request *http.Request) {
		conn, err := upgrader.Upgrade(writer, request, nil)
		if err != nil {
			return
		}
		go func() {
			remote := tunnel.Wrap(conn)
			defer remote.Close()
			if err := forwardHTTP(remote); err != nil && !isClosed(err) {
				log.Printf("SeaMoon function forwarding failed: %v", err)
			}
		}()
	})
	server := &http.Server{Addr: address, Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdownCtx)
	}()
	log.Printf("SeaMoon function server listening on %s", address)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}

// forwardHTTP is a focused adaptation of SeaMoon's HttpTransport. One
// WebSocket carries one ordinary HTTP-proxy TCP connection.
func forwardHTTP(client net.Conn) error {
	reader := bufio.NewReader(client)
	request, err := http.ReadRequest(reader)
	if err != nil {
		return err
	}
	defer request.Body.Close()

	target := strings.TrimSpace(request.Host)
	if target == "" && request.URL != nil {
		target = request.URL.Host
	}
	if target == "" {
		return errors.New("proxy request has no target host")
	}
	if _, _, err := net.SplitHostPort(target); err != nil {
		defaultPort := "80"
		if request.Method == http.MethodConnect || (request.URL != nil && request.URL.Scheme == "https") {
			defaultPort = "443"
		}
		target = net.JoinHostPort(strings.Trim(target, "[]"), defaultPort)
	}

	dialer := net.Dialer{Timeout: 10 * time.Second, KeepAlive: 30 * time.Second}
	destination, err := dialer.Dial("tcp", target)
	if err != nil {
		return fmt.Errorf("dial %s: %w", target, err)
	}
	defer destination.Close()

	if request.Method == http.MethodConnect {
		if _, err := io.WriteString(client, "HTTP/1.1 200 Connection established\r\n\r\n"); err != nil {
			return err
		}
	} else {
		request.Header.Del("Proxy-Connection")
		request.RequestURI = ""
		if err := request.Write(destination); err != nil {
			return err
		}
	}

	return relay(client, destination)
}

func relay(left, right net.Conn) error {
	errorsChannel := make(chan error, 2)
	copyConn := func(dst, src net.Conn) {
		_, err := io.Copy(dst, src)
		errorsChannel <- err
	}
	go copyConn(left, right)
	go copyConn(right, left)
	err := <-errorsChannel
	_ = left.Close()
	_ = right.Close()
	return err
}

func isClosed(err error) bool {
	if err == nil {
		return true
	}
	text := strings.ToLower(err.Error())
	return strings.Contains(text, "closed") || strings.Contains(text, "reset by peer") || strings.Contains(text, "unexpected eof")
}
