# OCI Service Limit Checker Function using OCI Python SDK

This function checks the service limits, usage and availability of all OCI resources across one or more regions in a tenancy. 
Once the function executes it will send a summary to the subscribed emails within your designated OCI Notification Topic. 
The function utilizes the OCI Python SDK and the OCI Functions Resource Principal for authentication.

Example output: "Service: database, Scope: AD, AD: UWQV:US-ASHBURN-AD-1, Limit Name: vm-standard1-ocpu-count, Limit: 4, Usage: 3, Available: 1, Usage %: 75.00%"

As you make your way through this tutorial, look out for this icon ![user input icon](./images/userinput.png).
Whenever you see it, it's time for you to perform an action.

## Prerequisites
Before you deploy this sample function, make sure you have run step A, B and C of the [Oracle Functions Quick Start Guide for Cloud Shell](https://www.oracle.com/webfolder/technetwork/tutorials/infographics/oci_functions_cloudshell_quickview/functions_quickview_top/functions_quickview/index.html)
* A - Set up your tenancy
* B - Create application
* C - Set up your Cloud Shell dev environment

## List Applications 
Assuming your have successfully completed the prerequisites, you should see your 
application in the list of applications.
```
fn ls apps
```

## Create or Update your Dynamic Group
In order to use other OCI Services, your function must be part of a dynamic group. For information on how to create a dynamic group, refer to the [documentation](https://docs.cloud.oracle.com/iaas/Content/Identity/Tasks/managingdynamicgroups.htm#To).

When specifying the *Matching Rules*, we suggest matching all functions in a compartment with:
```
ALL {resource.type = 'fnfunc', resource.compartment.id = 'ocid1.compartment.oc1..aaaaaxxxxx'}
```
Please check the [Accessing Other Oracle Cloud Infrastructure Resources from Running Functions](https://docs.cloud.oracle.com/en-us/iaas/Content/Functions/Tasks/functionsaccessingociresources.htm) for other *Matching Rules* options.


## Create or Update IAM Policies
Now that your dynamic group is created, create a new policy in root compartment that allows the dynamic group to use any resources you are interested in receiving
information about, in this case we will grant access to `read all-resources` in
the root compartment.

![user input icon](./images/userinput.png)

Your policy should look something like this:
```
Allow dynamic-group <dynamic-group-name> to read all-resources in tenancy 
Allow dynamic-group <dynamic-group-name> to use ons-topics in tenancy 
Allow dynamic-group <dynamic-group-name> to use ons-subscriptions in tenancy 
```

For more information on how to create policies, check the [documentation](https://docs.cloud.oracle.com/iaas/Content/Identity/Concepts/policysyntax.htm).


## Create Notifications Topic 
In order to recieve the summarized service limits of the tenancy, you'll need an OCI Notifications topic & Subscription for the data to be sent to. 

![user input icon](./images/userinput.png)

[Create an OCI Topic](https://docs.oracle.com/en-us/iaas/Content/Notification/Tasks/create-topic.htm#top)

Save the OCID of the OCI Topic you just created for use in the function. 

[Create an OCI Email Subscription](https://docs.oracle.com/en-us/iaas/Content/Notification/Tasks/create-subscription-email.htm#top)

NOTE - You must confirm the subscription email in your inbox after the subscription is created. 

### Clone the repository into Cloud Shell
You will need to clone the repository in Cloud Shell in order to build and deploy the function. 

![user input icon](./images/userinput.png)

Example: 
```
git clone https://github.com/webdev2080/ServiceLimitCheck.git
```


##Customize the test.json file
![user input icon](./images/userinput.png)
- Required - notification_topic_id: "<Topic OCID>"
- Optional - regions (Default is home region)
- Optional - threshold_percentage (Default is 90)

## Optional - Review and customize the function
Review the following files in the current folder:
* the code of the function, [func.py](./func.py)
* its dependencies, [requirements.txt](./requirements.txt)
* the function metadata, [func.yaml](./func.yaml)

## Deploy the function
In Cloud Shell, run the *fn deploy* command to build the function and its dependencies as a Docker image, 
push the image to OCIR, and deploy the function to Oracle Functions in your application.

![user input icon](./images/userinput.png)
```
fn -v deploy --app <app-name>
```

## Invoke the function

The function requires the following keys in the payload when invoked:
- *REQUIRED* - "notification_topic_id", the OCID of Notification Topic to send the summary.
- *Optional* - "threshold_percentage", the percentage threshold for which resources are nearing their limit. (Default is 90%)
- *Optional* - "regions", the specific regions to query. (Default is tenancy home region). - NOTE: (If querying a large amount of regions, function may time out due to amount of data being pulled). 

To run the function, you will need to invoke it with at least the notification_topic_id payload. 

![user input icon](./images/userinput.png)
```
fn invoke <app-name> <function-name> < test.json
```
e.g.:
```
fn invoke ServiceLimitApp ServiceLimitFunction < test.json
```

Assuming the other function was invoked correctly, you should see the following output after a few minutes :
```json
{"message": "Function executed successfully"}
```

Shortly after, the Notification Topic subscribers will recieve a message (email) with the summary of the resources sitting above the usage threshold. 

- *Optional* - You can run the OCI Function from any OCI CLI Authorized device with a Function Invoke Endpoint. Docs: https://docs.oracle.com/en-us/iaas/Content/Functions/Tasks/functionsinvokingfunctions.htm#rawrequestinvoke
```
e.g.: 
oci raw-request --http-method POST --target-uri <invoke-endpoint> --request-body "<request-parameters>"
```
