package tunnel

import (
	"net"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

// WebSocketConn adapts binary WebSocket messages to net.Conn. It is derived
// from SeaMoon's websocket tunnel wrapper and deliberately keeps one writer at
// a time because gorilla/websocket requires serialized writes.
type WebSocketConn struct {
	*websocket.Conn
	readBuffer []byte
	writeMu    sync.Mutex
}

func Wrap(conn *websocket.Conn) net.Conn {
	return &WebSocketConn{Conn: conn}
}

func (c *WebSocketConn) Read(buffer []byte) (int, error) {
	for len(c.readBuffer) == 0 {
		messageType, payload, err := c.Conn.ReadMessage()
		if err != nil {
			return 0, err
		}
		if messageType != websocket.BinaryMessage {
			continue
		}
		c.readBuffer = payload
	}
	n := copy(buffer, c.readBuffer)
	c.readBuffer = c.readBuffer[n:]
	return n, nil
}

func (c *WebSocketConn) Write(buffer []byte) (int, error) {
	c.writeMu.Lock()
	defer c.writeMu.Unlock()
	if err := c.Conn.WriteMessage(websocket.BinaryMessage, buffer); err != nil {
		return 0, err
	}
	return len(buffer), nil
}

func (c *WebSocketConn) LocalAddr() net.Addr  { return c.Conn.LocalAddr() }
func (c *WebSocketConn) RemoteAddr() net.Addr { return c.Conn.RemoteAddr() }
func (c *WebSocketConn) SetDeadline(deadline time.Time) error {
	if err := c.Conn.SetReadDeadline(deadline); err != nil {
		return err
	}
	return c.SetWriteDeadline(deadline)
}
func (c *WebSocketConn) SetReadDeadline(deadline time.Time) error {
	return c.Conn.SetReadDeadline(deadline)
}
func (c *WebSocketConn) SetWriteDeadline(deadline time.Time) error {
	c.writeMu.Lock()
	defer c.writeMu.Unlock()
	return c.Conn.SetWriteDeadline(deadline)
}
