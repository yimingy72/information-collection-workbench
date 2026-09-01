package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"strings"
	"syscall"

	"asset-workbench/seamoon-core/internal/cloud"
	"asset-workbench/seamoon-core/internal/gateway"
	functionserver "asset-workbench/seamoon-core/internal/server"
)

func main() {
	if len(os.Args) < 2 {
		fatal(errors.New("usage: seamoon-core <gateway|server|cloud>"))
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	switch os.Args[1] {
	case "gateway":
		runGateway(ctx, os.Args[2:])
	case "server":
		runServer(ctx, os.Args[2:])
	case "cloud":
		runCloud(os.Args[2:])
	case "version":
		fmt.Println("asset-workbench-seamoon-core 1")
	default:
		fatal(fmt.Errorf("unknown command %q", os.Args[1]))
	}
}

func runGateway(ctx context.Context, args []string) {
	flags := flag.NewFlagSet("gateway", flag.ExitOnError)
	proxyAddress := flags.String("proxy", "0.0.0.0:19080", "HTTP proxy listen address")
	adminAddress := flags.String("admin", "0.0.0.0:19081", "admin listen address")
	_ = flags.Parse(args)
	service := gateway.New()
	errorsChannel := make(chan error, 2)
	go func() { errorsChannel <- service.ServeProxy(ctx, *proxyAddress) }()
	go func() { errorsChannel <- service.ServeAdmin(ctx, *adminAddress) }()
	if err := <-errorsChannel; err != nil {
		fatal(err)
	}
}

func runServer(ctx context.Context, args []string) {
	flags := flag.NewFlagSet("server", flag.ExitOnError)
	port := flags.String("p", envOr("PORT", "9000"), "listen port")
	protocol := flags.String("t", "websocket", "tunnel protocol")
	_ = flags.Parse(args)
	if strings.ToLower(*protocol) != "websocket" {
		fatal(errors.New("this extraction only supports the websocket tunnel"))
	}
	if err := functionserver.Serve(ctx, "0.0.0.0:"+strings.TrimPrefix(*port, ":")); err != nil {
		fatal(err)
	}
}

func runCloud(args []string) {
	if len(args) != 1 || (args[0] != "deploy" && args[0] != "destroy") {
		fatal(errors.New("usage: seamoon-core cloud <deploy|destroy> with JSON on stdin"))
	}
	config, err := cloud.Decode(os.Stdin)
	if err != nil {
		fatal(err)
	}
	var result cloud.Result
	if args[0] == "deploy" {
		result, err = cloud.Deploy(config)
	} else {
		result, err = cloud.Destroy(config)
	}
	if err != nil {
		fatal(err)
	}
	if err := json.NewEncoder(os.Stdout).Encode(result); err != nil {
		fatal(err)
	}
}

func envOr(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

func fatal(err error) {
	log.Printf("error: %v", err)
	os.Exit(1)
}
