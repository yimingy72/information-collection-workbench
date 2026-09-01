package cloud

import (
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/tencentcloud/tencentcloud-sdk-go/tencentcloud/common"
	tencentErrors "github.com/tencentcloud/tencentcloud-sdk-go/tencentcloud/common/errors"
	"github.com/tencentcloud/tencentcloud-sdk-go/tencentcloud/common/profile"
	scf "github.com/tencentcloud/tencentcloud-sdk-go/tencentcloud/scf/v20180416"
)

const tencentNamespace = "seamoon"

type tencentTriggerDescription struct {
	AuthType  string            `json:"AuthType,omitempty"`
	NetConfig *tencentNetConfig `json:"NetConfig"`
}

type tencentNetConfig struct {
	EnableIntranet bool   `json:"EnableIntranet,omitempty"`
	EnableExtranet bool   `json:"EnableExtranet,omitempty"`
	ExtranetURL    string `json:"ExtranetUrl,omitempty"`
}

func tencentClient(config Config) (*scf.Client, error) {
	credential := common.NewCredential(config.AccessKeyID, config.AccessKeySecret)
	clientProfile := profile.NewClientProfile()
	clientProfile.HttpProfile.Endpoint = "scf.tencentcloudapi.com"
	return scf.NewClient(credential, config.Region, clientProfile)
}

func tencentInstanceConcurrencyConfig() *scf.InstanceConcurrencyConfig {
	return &scf.InstanceConcurrencyConfig{
		DynamicEnabled: common.StringPtr("FALSE"),
		MaxConcurrency: common.Uint64Ptr(seaMoonTencentConcurrency),
	}
}

func tencentFunctionConfigurationRequest(config Config) *scf.UpdateFunctionConfigurationRequest {
	return &scf.UpdateFunctionConfigurationRequest{
		Namespace:    common.StringPtr(tencentNamespace),
		FunctionName: common.StringPtr(config.FunctionName),
		Description:  common.StringPtr("asset-workbench-seamoon-websocket"),
		MemorySize:   common.Int64Ptr(int64(seaMoonMemoryMB)),
		Timeout:      common.Int64Ptr(600),
		ProtocolParams: &scf.ProtocolParams{WSParams: &scf.WSParams{
			IdleTimeOut: common.Uint64Ptr(60),
		}},
		PublicNetConfig: &scf.PublicNetConfigIn{
			PublicNetStatus: common.StringPtr("ENABLE"),
			EipConfig:       &scf.EipConfigIn{EipStatus: common.StringPtr("DISABLE")},
		},
		InstanceConcurrencyConfig: tencentInstanceConcurrencyConfig(),
	}
}

func deployTencent(config Config) (Result, error) {
	image := config.ImageURI
	if image == "" {
		image = tencentImages[config.Region]
	}
	if image == "" {
		return Result{}, fmt.Errorf("region %s has no SeaMoon preset image", config.Region)
	}
	client, err := tencentClient(config)
	if err != nil {
		return Result{}, err
	}
	namespace := scf.NewCreateNamespaceRequest()
	namespace.Namespace = common.StringPtr(tencentNamespace)
	namespace.Description = common.StringPtr("信息收集工作台 SeaMoon functions")
	if _, err := client.CreateNamespace(namespace); err != nil {
		cloudError, ok := err.(*tencentErrors.TencentCloudSDKError)
		if !ok || cloudError.Code != scf.RESOURCEINUSE_NAMESPACE {
			return Result{}, fmt.Errorf("create Tencent Cloud namespace: %w", err)
		}
	}

	request := scf.NewCreateFunctionRequest()
	request.Namespace = common.StringPtr(tencentNamespace)
	request.FunctionName = common.StringPtr(config.FunctionName)
	request.Description = common.StringPtr("asset-workbench-seamoon-websocket")
	request.Type = common.StringPtr("HTTP")
	request.MemorySize = common.Int64Ptr(int64(seaMoonMemoryMB))
	request.ProtocolType = common.StringPtr("WS")
	request.ProtocolParams = &scf.ProtocolParams{WSParams: &scf.WSParams{IdleTimeOut: common.Uint64Ptr(60)}}
	request.Timeout = common.Int64Ptr(600)
	request.AutoCreateClsTopic = common.StringPtr("FALSE")
	request.Code = &scf.Code{ImageConfig: &scf.ImageConfig{
		ImageType: common.StringPtr("personal"), ImageUri: common.StringPtr(image),
		Args: common.StringPtr("server -p 9000 -t websocket"), ImagePort: common.Int64Ptr(9000),
	}}
	request.PublicNetConfig = &scf.PublicNetConfigIn{
		PublicNetStatus: common.StringPtr("ENABLE"),
		EipConfig:       &scf.EipConfigIn{EipStatus: common.StringPtr("DISABLE")},
	}
	request.InstanceConcurrencyConfig = tencentInstanceConcurrencyConfig()
	if _, err := client.CreateFunction(request); err != nil {
		cloudError, ok := err.(*tencentErrors.TencentCloudSDKError)
		if !ok || cloudError.Code != scf.RESOURCEINUSE_FUNCTION {
			return Result{}, fmt.Errorf("create Tencent Cloud function: %w", err)
		}
		if _, updateErr := client.UpdateFunctionConfiguration(tencentFunctionConfigurationRequest(config)); updateErr != nil {
			return Result{}, fmt.Errorf("update existing Tencent Cloud function resources: %w", updateErr)
		}
	}

	deploymentID := ""
	active := false
	for attempt := 0; attempt < 30; attempt++ {
		listRequest := scf.NewListFunctionsRequest()
		listRequest.Namespace = common.StringPtr(tencentNamespace)
		listRequest.SearchKey = common.StringPtr(config.FunctionName)
		functions, err := client.ListFunctions(listRequest)
		if err != nil {
			return Result{}, fmt.Errorf("read Tencent Cloud function status: %w", err)
		}
		if functions.Response != nil {
			for _, function := range functions.Response.Functions {
				if function.FunctionName == nil || *function.FunctionName != config.FunctionName {
					continue
				}
				if function.FunctionId != nil {
					deploymentID = *function.FunctionId
				}
				if function.Status != nil && *function.Status == "Active" {
					active = true
					break
				}
				if function.Status != nil && *function.Status != "Creating" {
					description := "unknown status"
					if function.StatusDesc != nil {
						description = *function.StatusDesc
					}
					return Result{}, fmt.Errorf("Tencent Cloud function status %s: %s", *function.Status, description)
				}
			}
		}
		if active {
			break
		}
		time.Sleep(2 * time.Second)
	}
	if !active {
		return Result{}, errors.New("Tencent Cloud function did not become active within 60 seconds")
	}

	triggerDescription, _ := json.Marshal(tencentTriggerDescription{
		AuthType: "NONE", NetConfig: &tencentNetConfig{EnableIntranet: false, EnableExtranet: true},
	})
	triggerRequest := scf.NewCreateTriggerRequest()
	triggerRequest.TriggerName = common.StringPtr("http")
	triggerRequest.FunctionName = common.StringPtr(config.FunctionName)
	triggerRequest.Type = common.StringPtr("http")
	triggerRequest.TriggerDesc = common.StringPtr(string(triggerDescription))
	triggerRequest.Namespace = common.StringPtr(tencentNamespace)
	trigger, err := client.CreateTrigger(triggerRequest)
	if err != nil {
		return Result{}, fmt.Errorf("create Tencent Cloud function URL: %w", err)
	}
	if trigger.Response == nil || trigger.Response.TriggerInfo == nil || trigger.Response.TriggerInfo.TriggerDesc == nil {
		return Result{}, errors.New("Tencent Cloud did not return a function URL")
	}
	var description tencentTriggerDescription
	if err := json.Unmarshal([]byte(*trigger.Response.TriggerInfo.TriggerDesc), &description); err != nil {
		return Result{}, fmt.Errorf("decode Tencent Cloud function URL: %w", err)
	}
	if description.NetConfig == nil || description.NetConfig.ExtranetURL == "" {
		return Result{}, errors.New("Tencent Cloud did not return an extranet URL")
	}
	return Result{Endpoint: description.NetConfig.ExtranetURL, DeploymentID: deploymentID, Message: "deployed"}, nil
}

func destroyTencent(config Config) (Result, error) {
	client, err := tencentClient(config)
	if err != nil {
		return Result{}, err
	}
	request := scf.NewDeleteFunctionRequest()
	request.Namespace = common.StringPtr(tencentNamespace)
	request.FunctionName = common.StringPtr(config.FunctionName)
	if _, err := client.DeleteFunction(request); err != nil {
		return Result{}, fmt.Errorf("delete Tencent Cloud function: %w", err)
	}
	return Result{Message: "deleted"}, nil
}
