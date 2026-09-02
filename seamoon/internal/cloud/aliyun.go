package cloud

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	openapiutil "github.com/alibabacloud-go/darabonba-openapi/v2/utils"
	fc "github.com/alibabacloud-go/fc-20230330/v4/client"
	"github.com/alibabacloud-go/tea/dara"
	"github.com/alibabacloud-go/tea/tea"
)

type aliyunTriggerConfig struct {
	Methods            []string `json:"methods"`
	AuthType           string   `json:"authType"`
	DisableURLInternet bool     `json:"disableURLInternet"`
}

func aliyunClient(config Config) (*fc.Client, error) {
	return fc.NewClient(&openapiutil.Config{
		AccessKeyId:     tea.String(config.AccessKeyID),
		AccessKeySecret: tea.String(config.AccessKeySecret),
		Endpoint:        tea.String(fmt.Sprintf("fcv3.%s.aliyuncs.com", config.Region)),
	})
}

const aliyunTriggerName = "websocket"

func aliyunImage(config Config) (string, error) {
	image := config.ImageURI
	if image == "" {
		image = aliyunImages[config.Region]
	}
	if image == "" {
		return "", fmt.Errorf("region %s has no SeaMoon preset image", config.Region)
	}
	return image, nil
}

func aliyunContainerConfig(image string) *fc.CustomContainerConfig {
	return &fc.CustomContainerConfig{
		Image: tea.String(image),
		Port:  tea.Int32(9000),
		Command: []*string{
			tea.String("server"), tea.String("-p"), tea.String("9000"), tea.String("-t"), tea.String("websocket"),
		},
	}
}

func aliyunCreateFunctionInput(config Config, image string) *fc.CreateFunctionInput {
	return &fc.CreateFunctionInput{
		FunctionName:          tea.String(config.FunctionName),
		Description:           tea.String("asset-workbench-seamoon-websocket"),
		Runtime:               tea.String("custom-container"),
		Handler:               tea.String("main"),
		CustomContainerConfig: aliyunContainerConfig(image),
		Timeout:               tea.Int32(300),
		DiskSize:              tea.Int32(seaMoonDiskMB),
		Cpu:                   tea.Float32(seaMoonCPU),
		MemorySize:            tea.Int32(seaMoonMemoryMB),
		InstanceConcurrency:   tea.Int32(seaMoonInstanceConcurrency),
	}
}

func aliyunUpdateFunctionInput(image string) *fc.UpdateFunctionInput {
	return &fc.UpdateFunctionInput{
		Description:           tea.String("asset-workbench-seamoon-websocket"),
		CustomContainerConfig: aliyunContainerConfig(image),
		Timeout:               tea.Int32(300),
		DiskSize:              tea.Int32(seaMoonDiskMB),
		Cpu:                   tea.Float32(seaMoonCPU),
		MemorySize:            tea.Int32(seaMoonMemoryMB),
		InstanceConcurrency:   tea.Int32(seaMoonInstanceConcurrency),
	}
}

func aliyunResourceAlreadyExists(err error) bool {
	if err == nil {
		return false
	}
	var daraErr *dara.SDKError
	if errors.As(err, &daraErr) {
		code := dara.StringValue(daraErr.GetCode())
		if code == "FunctionAlreadyExists" || code == "TriggerAlreadyExists" {
			return true
		}
	}
	var teaErr *tea.SDKError
	if errors.As(err, &teaErr) {
		code := tea.StringValue(teaErr.Code)
		if code == "FunctionAlreadyExists" || code == "TriggerAlreadyExists" {
			return true
		}
	}
	message := strings.ToLower(err.Error())
	return strings.Contains(message, "functionalreadyexists") || strings.Contains(message, "triggeralreadyexists")
}

func aliyunTriggerConfigJSON() (string, error) {
	triggerBytes, err := json.Marshal(aliyunTriggerConfig{
		Methods: []string{"GET", "POST"}, AuthType: "anonymous", DisableURLInternet: false,
	})
	if err != nil {
		return "", fmt.Errorf("encode Alibaba Cloud HTTP trigger: %w", err)
	}
	return string(triggerBytes), nil
}

func aliyunTriggerEndpoint(trigger *fc.Trigger) (string, error) {
	if trigger == nil || trigger.HttpTrigger == nil || trigger.HttpTrigger.UrlInternet == nil {
		return "", errors.New("Alibaba Cloud did not return an Internet trigger URL")
	}
	endpoint := dara.StringValue(trigger.HttpTrigger.UrlInternet)
	if endpoint == "" {
		return "", errors.New("Alibaba Cloud did not return an Internet trigger URL")
	}
	if !strings.HasPrefix(endpoint, "http://") && !strings.HasPrefix(endpoint, "https://") {
		endpoint = "https://" + endpoint
	}
	return endpoint, nil
}

func deployAliyun(config Config) (Result, error) {
	client, err := aliyunClient(config)
	if err != nil {
		return Result{}, err
	}
	image, err := aliyunImage(config)
	if err != nil {
		return Result{}, err
	}

	response, err := client.CreateFunction(&fc.CreateFunctionRequest{
		Body: aliyunCreateFunctionInput(config, image),
	})
	deploymentID := ""
	if err == nil && response != nil && response.Body != nil && response.Body.FunctionId != nil {
		deploymentID = dara.StringValue(response.Body.FunctionId)
	}
	if err != nil {
		if !aliyunResourceAlreadyExists(err) {
			return Result{}, fmt.Errorf("create Alibaba Cloud function: %w", err)
		}

		// A previous deployment may have created the function but failed while
		// creating its trigger. Reuse that function instead of treating the
		// retry as a fatal error.
		existing, getErr := client.GetFunction(tea.String(config.FunctionName), &fc.GetFunctionRequest{})
		if getErr != nil {
			return Result{}, fmt.Errorf("read existing Alibaba Cloud function: %w", getErr)
		}
		if existing == nil || existing.Body == nil {
			return Result{}, errors.New("Alibaba Cloud function exists but could not be read")
		}
		if existing.Body.FunctionId != nil {
			deploymentID = dara.StringValue(existing.Body.FunctionId)
		}
		if _, updateErr := client.UpdateFunction(tea.String(config.FunctionName), &fc.UpdateFunctionRequest{
			Body: aliyunUpdateFunctionInput(image),
		}); updateErr != nil {
			return Result{}, fmt.Errorf("update existing Alibaba Cloud function resources: %w", updateErr)
		}
	}

	triggerConfig, err := aliyunTriggerConfigJSON()
	if err != nil {
		return Result{}, err
	}
	createdTrigger, err := client.CreateTrigger(tea.String(config.FunctionName), &fc.CreateTriggerRequest{
		Body: &fc.CreateTriggerInput{
			TriggerName: tea.String(aliyunTriggerName), TriggerType: tea.String("http"), TriggerConfig: tea.String(triggerConfig),
		},
	})
	var triggerBody *fc.Trigger
	if err != nil {
		if !aliyunResourceAlreadyExists(err) {
			return Result{}, fmt.Errorf("create Alibaba Cloud HTTP trigger: %w", err)
		}

		existing, getErr := client.GetTrigger(tea.String(config.FunctionName), tea.String(aliyunTriggerName))
		if getErr != nil {
			return Result{}, fmt.Errorf("read existing Alibaba Cloud HTTP trigger: %w", getErr)
		}
		if existing != nil {
			triggerBody = existing.Body
		}
	} else if createdTrigger != nil {
		triggerBody = createdTrigger.Body
	}

	endpoint, err := aliyunTriggerEndpoint(triggerBody)
	if err != nil {
		return Result{}, err
	}
	return Result{Endpoint: endpoint, DeploymentID: deploymentID, Message: "deployed"}, nil
}

func destroyAliyun(config Config) (Result, error) {
	client, err := aliyunClient(config)
	if err != nil {
		return Result{}, err
	}
	triggers, err := client.ListTriggers(tea.String(config.FunctionName), &fc.ListTriggersRequest{})
	if err != nil {
		return Result{}, fmt.Errorf("list Alibaba Cloud triggers: %w", err)
	}
	if triggers.Body != nil {
		for _, trigger := range triggers.Body.Triggers {
			if trigger != nil && trigger.TriggerName != nil {
				if _, err := client.DeleteTrigger(tea.String(config.FunctionName), trigger.TriggerName); err != nil {
					return Result{}, fmt.Errorf("delete Alibaba Cloud trigger: %w", err)
				}
			}
		}
	}
	if _, err := client.DeleteFunction(tea.String(config.FunctionName)); err != nil {
		return Result{}, fmt.Errorf("delete Alibaba Cloud function: %w", err)
	}
	return Result{Message: "deleted"}, nil
}
