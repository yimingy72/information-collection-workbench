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
const tencentTriggerName = "http"

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

func tencentErrorCode(err error) string {
	var cloudError *tencentErrors.TencentCloudSDKError
	if errors.As(err, &cloudError) {
		return cloudError.Code
	}
	return ""
}

func tencentImage(config Config) (string, error) {
	image := config.ImageURI
	if image == "" {
		image = tencentImages[config.Region]
	}
	if image == "" {
		return "", fmt.Errorf("region %s has no SeaMoon preset image", config.Region)
	}
	return image, nil
}

func tencentImageConfig(image string) *scf.ImageConfig {
	// Official SeaMoon images start via ENTRYPOINT ["/app/entrypoint.sh"]
	// which execs /app/seamoon. Tencent SCF ImageConfig.Command replaces
	// Entrypoint; Args replace CMD. Some regions execute Args[0] directly
	// when Command is empty, so "server" is not found. Set both explicitly.
	return &scf.ImageConfig{
		ImageType: common.StringPtr("personal"),
		ImageUri:  common.StringPtr(image),
		Command:   common.StringPtr("/app/seamoon"),
		Args:      common.StringPtr("server -p 9000 -t websocket"),
		ImagePort: common.Int64Ptr(9000),
	}
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

func tencentCreateFunctionRequest(config Config, image string) *scf.CreateFunctionRequest {
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
	request.Code = &scf.Code{ImageConfig: tencentImageConfig(image)}
	request.PublicNetConfig = &scf.PublicNetConfigIn{
		PublicNetStatus: common.StringPtr("ENABLE"),
		EipConfig:       &scf.EipConfigIn{EipStatus: common.StringPtr("DISABLE")},
	}
	request.InstanceConcurrencyConfig = tencentInstanceConcurrencyConfig()
	return request
}

func waitTencentFunctionActive(client *scf.Client, config Config) (string, error) {
	deploymentID := ""
	for attempt := 0; attempt < 30; attempt++ {
		listRequest := scf.NewListFunctionsRequest()
		listRequest.Namespace = common.StringPtr(tencentNamespace)
		listRequest.SearchKey = common.StringPtr(config.FunctionName)
		functions, err := client.ListFunctions(listRequest)
		if err != nil {
			return "", fmt.Errorf("read Tencent Cloud function status: %w", err)
		}
		if functions.Response != nil {
			for _, function := range functions.Response.Functions {
				if function.FunctionName == nil || *function.FunctionName != config.FunctionName {
					continue
				}
				if function.FunctionId != nil {
					deploymentID = *function.FunctionId
				}
				status := ""
				if function.Status != nil {
					status = *function.Status
				}
				switch status {
				case "Active":
					return deploymentID, nil
				case "", "Creating", "Updating":
					// Wait for the function or image update to finish.
				default:
					description := "unknown status"
					if function.StatusDesc != nil {
						description = *function.StatusDesc
					}
					return "", fmt.Errorf("Tencent Cloud function status %s: %s", status, description)
				}
			}
		}
		time.Sleep(2 * time.Second)
	}
	return "", errors.New("Tencent Cloud function did not become active within 60 seconds")
}

func tencentExtranetURL(raw string) (string, error) {
	var description tencentTriggerDescription
	if err := json.Unmarshal([]byte(raw), &description); err != nil {
		return "", fmt.Errorf("decode Tencent Cloud function URL: %w", err)
	}
	if description.NetConfig == nil || description.NetConfig.ExtranetURL == "" {
		return "", errors.New("Tencent Cloud did not return an extranet URL")
	}
	return description.NetConfig.ExtranetURL, nil
}

func ensureTencentTrigger(client *scf.Client, config Config) (string, error) {
	triggerDescription, err := json.Marshal(tencentTriggerDescription{
		AuthType: "NONE", NetConfig: &tencentNetConfig{EnableIntranet: false, EnableExtranet: true},
	})
	if err != nil {
		return "", fmt.Errorf("encode Tencent Cloud HTTP trigger: %w", err)
	}
	triggerRequest := scf.NewCreateTriggerRequest()
	triggerRequest.TriggerName = common.StringPtr(tencentTriggerName)
	triggerRequest.FunctionName = common.StringPtr(config.FunctionName)
	triggerRequest.Type = common.StringPtr("http")
	triggerRequest.TriggerDesc = common.StringPtr(string(triggerDescription))
	triggerRequest.Namespace = common.StringPtr(tencentNamespace)
	trigger, err := client.CreateTrigger(triggerRequest)
	if err == nil {
		if trigger.Response == nil || trigger.Response.TriggerInfo == nil || trigger.Response.TriggerInfo.TriggerDesc == nil {
			return "", errors.New("Tencent Cloud did not return a function URL")
		}
		return tencentExtranetURL(*trigger.Response.TriggerInfo.TriggerDesc)
	}
	code := tencentErrorCode(err)
	if code != scf.RESOURCEINUSE_TRIGGER && code != scf.RESOURCEINUSE_TRIGGERNAME {
		return "", fmt.Errorf("create Tencent Cloud function URL: %w", err)
	}

	listRequest := scf.NewListTriggersRequest()
	listRequest.FunctionName = common.StringPtr(config.FunctionName)
	listRequest.Namespace = common.StringPtr(tencentNamespace)
	listRequest.Limit = common.Uint64Ptr(20)
	listed, listErr := client.ListTriggers(listRequest)
	if listErr != nil {
		return "", fmt.Errorf("read existing Tencent Cloud HTTP trigger: %w", listErr)
	}
	if listed.Response != nil {
		for _, item := range listed.Response.Triggers {
			if item == nil || item.TriggerDesc == nil {
				continue
			}
			if item.Type != nil && *item.Type != "http" {
				continue
			}
			if item.TriggerName != nil && *item.TriggerName != "" && *item.TriggerName != tencentTriggerName {
				continue
			}
			endpoint, parseErr := tencentExtranetURL(*item.TriggerDesc)
			if parseErr == nil {
				return endpoint, nil
			}
		}
	}
	return "", errors.New("Tencent Cloud HTTP trigger already exists but no extranet URL was returned")
}

func deployTencent(config Config) (Result, error) {
	image, err := tencentImage(config)
	if err != nil {
		return Result{}, err
	}
	client, err := tencentClient(config)
	if err != nil {
		return Result{}, err
	}
	namespace := scf.NewCreateNamespaceRequest()
	namespace.Namespace = common.StringPtr(tencentNamespace)
	namespace.Description = common.StringPtr("信息收集工作台 SeaMoon functions")
	if _, err := client.CreateNamespace(namespace); err != nil {
		if tencentErrorCode(err) != scf.RESOURCEINUSE_NAMESPACE {
			return Result{}, fmt.Errorf("create Tencent Cloud namespace: %w", err)
		}
	}

	if _, err := client.CreateFunction(tencentCreateFunctionRequest(config, image)); err != nil {
		code := tencentErrorCode(err)
		if code != scf.RESOURCEINUSE_FUNCTION && code != scf.RESOURCEINUSE_FUNCTIONNAME {
			return Result{}, fmt.Errorf("create Tencent Cloud function: %w", err)
		}
		if _, updateErr := client.UpdateFunctionConfiguration(tencentFunctionConfigurationRequest(config)); updateErr != nil {
			return Result{}, fmt.Errorf("update existing Tencent Cloud function resources: %w", updateErr)
		}
		updateCode := scf.NewUpdateFunctionCodeRequest()
		updateCode.Namespace = common.StringPtr(tencentNamespace)
		updateCode.FunctionName = common.StringPtr(config.FunctionName)
		updateCode.Code = &scf.Code{ImageConfig: tencentImageConfig(image)}
		if _, updateErr := client.UpdateFunctionCode(updateCode); updateErr != nil {
			return Result{}, fmt.Errorf("update existing Tencent Cloud function image: %w", updateErr)
		}
	}

	deploymentID, err := waitTencentFunctionActive(client, config)
	if err != nil {
		return Result{}, err
	}
	endpoint, err := ensureTencentTrigger(client, config)
	if err != nil {
		return Result{}, err
	}
	return Result{Endpoint: endpoint, DeploymentID: deploymentID, Message: "deployed"}, nil
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
