package cloud

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
)

// SeaMoon is an I/O-bound WebSocket HTTP tunnel. Keep the function small,
// allow one instance to carry the five-request ICP batch plus one spare slot,
// and leave scale-to-zero enabled by not creating any provisioned instances.
const (
	seaMoonCPU                 float32 = 0.1
	seaMoonMemoryMB            int32   = 128
	seaMoonDiskMB              int32   = 512
	seaMoonInstanceConcurrency int32   = 6
	seaMoonTencentConcurrency  uint64  = 6
)

type Config struct {
	Provider        string `json:"provider"`
	AccessKeyID     string `json:"access_key_id"`
	AccessKeySecret string `json:"access_key_secret"`
	Region          string `json:"region"`
	FunctionName    string `json:"function_name"`
	ImageURI        string `json:"image_uri"`
	DeploymentID    string `json:"deployment_id"`
}

type Result struct {
	Endpoint     string `json:"endpoint,omitempty"`
	DeploymentID string `json:"deployment_id,omitempty"`
	Message      string `json:"message,omitempty"`
}

func Decode(reader io.Reader) (Config, error) {
	var config Config
	decoder := json.NewDecoder(io.LimitReader(reader, 256*1024))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&config); err != nil {
		return config, err
	}
	config.Provider = strings.ToLower(strings.TrimSpace(config.Provider))
	config.AccessKeyID = strings.TrimSpace(config.AccessKeyID)
	config.AccessKeySecret = strings.TrimSpace(config.AccessKeySecret)
	config.Region = strings.TrimSpace(config.Region)
	config.FunctionName = strings.TrimSpace(config.FunctionName)
	config.ImageURI = strings.TrimSpace(config.ImageURI)
	if config.AccessKeyID == "" || config.AccessKeySecret == "" {
		return config, errors.New("access key id and secret are required")
	}
	if config.Region == "" || config.FunctionName == "" {
		return config, errors.New("region and function name are required")
	}
	return config, nil
}

func Deploy(config Config) (Result, error) {
	switch config.Provider {
	case "aliyun":
		return deployAliyun(config)
	case "tencent":
		return deployTencent(config)
	default:
		return Result{}, fmt.Errorf("unsupported cloud provider %q", config.Provider)
	}
}

func Destroy(config Config) (Result, error) {
	switch config.Provider {
	case "aliyun":
		return destroyAliyun(config)
	case "tencent":
		return destroyTencent(config)
	default:
		return Result{}, fmt.Errorf("unsupported cloud provider %q", config.Provider)
	}
}
