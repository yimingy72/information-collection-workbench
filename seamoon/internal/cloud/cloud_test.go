package cloud

import (
	"encoding/json"
	"strings"
	"testing"

	fc "github.com/alibabacloud-go/fc-20230330/v4/client"
	"github.com/alibabacloud-go/tea/dara"
	"github.com/alibabacloud-go/tea/tea"
)

func TestDecodeTrimsConfiguration(t *testing.T) {
	config, err := Decode(strings.NewReader(`{
        "provider":" aliyun ",
        "access_key_id":" ak ",
        "access_key_secret":" secret ",
        "region":" cn-hangzhou ",
        "function_name":" workbench ",
        "image_uri":"",
        "deployment_id":""
    }`))
	if err != nil {
		t.Fatal(err)
	}
	if config.Provider != "aliyun" || config.AccessKeyID != "ak" || config.FunctionName != "workbench" {
		t.Fatalf("unexpected config: %#v", config)
	}
}

func TestPresetImagesCoverDefaultRegions(t *testing.T) {
	if aliyunImages["cn-hangzhou"] == "" {
		t.Fatal("missing Alibaba Cloud preset image")
	}
	if tencentImages["ap-guangzhou"] == "" {
		t.Fatal("missing Tencent Cloud preset image")
	}
}

func TestAliyunResourceAlreadyExistsRecognizesDaraSDKCode(t *testing.T) {
	err := dara.NewSDKError(map[string]interface{}{
		"code":    "FunctionAlreadyExists",
		"message": "function already exists",
	})
	if !aliyunResourceAlreadyExists(err) {
		t.Fatal("expected FunctionAlreadyExists to be treated as idempotent")
	}
}

func TestAliyunResourceAlreadyExistsRecognizesTeaSDKCode(t *testing.T) {
	err := tea.NewSDKError(map[string]interface{}{
		"code":    "TriggerAlreadyExists",
		"message": "trigger already exists",
	})
	if !aliyunResourceAlreadyExists(err) {
		t.Fatal("expected TriggerAlreadyExists to be treated as idempotent")
	}
}

func TestAliyunTriggerEndpointNormalizesHostOnlyURL(t *testing.T) {
	endpoint, err := aliyunTriggerEndpoint(&fc.Trigger{
		HttpTrigger: &fc.HTTPTrigger{UrlInternet: tea.String("example.aliyuncs.com")},
	})
	if err != nil {
		t.Fatal(err)
	}
	if endpoint != "https://example.aliyuncs.com" {
		t.Fatalf("unexpected endpoint: %q", endpoint)
	}
}

func TestManagedFunctionSizing(t *testing.T) {
	aliyun := aliyunCreateFunctionInput(Config{FunctionName: "test"}, "image")
	if aliyun.CustomContainerConfig == nil || aliyun.CustomContainerConfig.Image == nil ||
		*aliyun.CustomContainerConfig.Image != "image" {
		t.Fatalf("expected Alibaba custom container image in create request: %#v", aliyun.CustomContainerConfig)
	}
	if aliyun.Cpu == nil || *aliyun.Cpu != seaMoonCPU {
		t.Fatalf("unexpected Alibaba CPU: %v", aliyun.Cpu)
	}
	if aliyun.MemorySize == nil || *aliyun.MemorySize != seaMoonMemoryMB {
		t.Fatalf("unexpected Alibaba memory: %v", aliyun.MemorySize)
	}
	if aliyun.DiskSize == nil || *aliyun.DiskSize != seaMoonDiskMB {
		t.Fatalf("unexpected Alibaba disk: %v", aliyun.DiskSize)
	}
	if aliyun.InstanceConcurrency == nil || *aliyun.InstanceConcurrency != seaMoonInstanceConcurrency {
		t.Fatalf("unexpected Alibaba concurrency: %v", aliyun.InstanceConcurrency)
	}
	encoded, err := json.Marshal(aliyun)
	if err != nil {
		t.Fatalf("marshal Alibaba create request: %v", err)
	}
	if !strings.Contains(string(encoded), `"customContainerConfig"`) {
		t.Fatalf("Alibaba create request omitted customContainerConfig: %s", encoded)
	}
	t.Logf("Alibaba CreateFunction body: %s", encoded)

	updated := aliyunUpdateFunctionInput("image")
	if updated.CustomContainerConfig == nil || updated.CustomContainerConfig.Image == nil ||
		*updated.CustomContainerConfig.Image != "image" {
		t.Fatalf("expected Alibaba custom container image in update request: %#v", updated.CustomContainerConfig)
	}
	encoded, err = json.Marshal(updated)
	if err != nil {
		t.Fatalf("marshal Alibaba update request: %v", err)
	}
	if !strings.Contains(string(encoded), `"customContainerConfig"`) {
		t.Fatalf("Alibaba update request omitted customContainerConfig: %s", encoded)
	}
	t.Logf("Alibaba UpdateFunction body: %s", encoded)
	if updated.Cpu == nil || *updated.Cpu != seaMoonCPU ||
		updated.MemorySize == nil || *updated.MemorySize != seaMoonMemoryMB ||
		updated.DiskSize == nil || *updated.DiskSize != seaMoonDiskMB ||
		updated.InstanceConcurrency == nil || *updated.InstanceConcurrency != seaMoonInstanceConcurrency {
		t.Fatalf("unexpected Alibaba update sizing: %#v", updated)
	}

	tencent := tencentInstanceConcurrencyConfig()
	if tencent.MaxConcurrency == nil || *tencent.MaxConcurrency != seaMoonTencentConcurrency {
		t.Fatalf("unexpected Tencent concurrency: %v", tencent.MaxConcurrency)
	}
	config := tencentFunctionConfigurationRequest(Config{FunctionName: "test"})
	if config.MemorySize == nil || *config.MemorySize != int64(seaMoonMemoryMB) {
		t.Fatalf("unexpected Tencent memory: %v", config.MemorySize)
	}
	if config.InstanceConcurrencyConfig == nil || config.InstanceConcurrencyConfig.MaxConcurrency == nil ||
		*config.InstanceConcurrencyConfig.MaxConcurrency != seaMoonTencentConcurrency {
		t.Fatalf("unexpected Tencent update sizing: %#v", config.InstanceConcurrencyConfig)
	}
}
