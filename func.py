import oci
import datetime
import json
import logging
import io

from fdk import response

# Set the default usage threshold percentage
DEFAULT_THRESHOLD_PERCENTAGE = 90
# Set the default policy limit
DEFAULT_POLICY_LIMIT = 100

# Initialize summary buffer for log entries
summary_buffer = []
error_buffer = []
logged_entries = set()  # Set to track logged entries and avoid duplicates

# Function to log messages to both console and buffer (to be sent via email)
def log_message(message):
    logging.getLogger().info(message)
    summary_buffer.append(message)

# Function to add errors to the error log buffer (to be optionally sent via email)
def add_to_error_log(message):
    error_buffer.append(message)

# Function to send the summary via OCI Notifications
def send_notification(notification_topic_id, signer):
    summary_message = "\n".join(summary_buffer)
    error_message = "\n".join(error_buffer)

    if not summary_message:
        summary_message = "No resources are near their usage limits."

    # Construct the full notification message
    notification_message = f"OCI Resource Usage Report - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    
    if error_message:
        notification_message += f"Errors encountered:\n{error_message}\n\n"

    notification_message += summary_message

    # Truncate the message if it's too large
    max_message_size = 65536  # 64 KiB
    if len(notification_message) > max_message_size:
        notification_message = notification_message[:max_message_size - 1000] + "\n\n[Message Truncated]"

    notification_client = oci.ons.NotificationDataPlaneClient({}, signer=signer)
    try:
        response = notification_client.publish_message(
            topic_id=notification_topic_id,
            message_details=oci.ons.models.MessageDetails(
                title="OCI Resource Usage Alert",
                body=notification_message
            )
        )
    except oci.exceptions.ServiceError as e:
        error_message = f"Failed to send notification: {str(e)}"
        log_message(error_message)
        add_to_error_log(error_message)

# Function to list all available services
def list_all_services(compartment_id, limits_client, limit=1000):
    services = []
    next_page = None
    while True:
        services_response = limits_client.list_services(
            compartment_id=compartment_id,
            page=next_page,
            limit=limit
        )
        services.extend(services_response.data)
        
        next_page = services_response.headers.get('opc-next-page')
        if not next_page:
            break
    return services

# Function to list all availability domains in the tenancy
def list_availability_domains(identity_client, compartment_id):
    availability_domains = []
    ads_response = identity_client.list_availability_domains(compartment_id=compartment_id)
    availability_domains.extend([ad.name for ad in ads_response.data])
    return availability_domains

# Function to get all limit definitions for a specific service
def get_all_limit_definitions(service_name, compartment_id, limits_client, limit=1000):
    limit_definitions = []
    next_page = None
    while True:
        limit_definitions_response = limits_client.list_limit_definitions(
            compartment_id=compartment_id,
            service_name=service_name,
            page=next_page,
            limit=limit
        )

        limit_definitions.extend(limit_definitions_response.data)
        next_page = limit_definitions_response.headers.get('opc-next-page')
        if not next_page:
            break
    return limit_definitions

# Function to get all limit values for a service
def get_all_limit_values(service_name, compartment_id, limits_client, limit=1000):
    limit_values = []
    next_page = None
    while True:
        limit_values_response = limits_client.list_limit_values(
            compartment_id=compartment_id,
            service_name=service_name,
            page=next_page,
            limit=limit
        )

        limit_values.extend(limit_values_response.data)
        next_page = limit_values_response.headers.get('opc-next-page')
        if not next_page:
            break
    return limit_values

# Function to get resource availability based on scope type
def get_resource_availability(service_name, limit_name, compartment_id, limits_client, availability_domain=None, scope_type=None):
    try:
        if scope_type == 'AD':
            availability_data = limits_client.get_resource_availability(
                compartment_id=compartment_id,
                service_name=service_name,
                limit_name=limit_name,
                availability_domain=availability_domain
            ).data
        else:
            availability_data = limits_client.get_resource_availability(
                compartment_id=compartment_id,
                service_name=service_name,
                limit_name=limit_name
            ).data

        return availability_data.used, availability_data.available
    except oci.exceptions.ServiceError as e:
        log_message(f"Error fetching resource availability: {str(e)}")
        return None, None

# Function to log usage if above threshold into a summary buffer
def log_usage_if_above_threshold(service_name, scope_type, availability_domain, limit_name, service_limit, usage, available, threshold_percentage):
    if service_limit > 0:  # To avoid division by zero
        usage_percentage = (usage / service_limit) * 100
        entry_key = (service_name, scope_type, availability_domain, limit_name)
        
        if entry_key not in logged_entries and usage_percentage >= threshold_percentage:
            log_entry = (f"Service: {service_name}, Scope: {scope_type}, AD: {availability_domain or 'N/A'}, "
                         f"Limit Name: {limit_name}, Limit: {service_limit}, Usage: {usage}, Available: {available}, "
                         f"Usage %: {usage_percentage:.2f}%")
            log_message(log_entry)
            logged_entries.add(entry_key)

# Function to count policies in the tenancy
def count_policies(identity_client, compartment_id):
    policies = []
    next_page = None
    
    while True:
        response = identity_client.list_policies(compartment_id=compartment_id, page=next_page)
        policies.extend(response.data)
        
        next_page = response.headers.get('opc-next-page')
        if not next_page:
            break

    return len(policies)

# Function to check policy limits
def check_policy_limits(identity_client, policy_limit, compartment_id):
    policy_count = count_policies(identity_client, compartment_id)
    log_message(f"Total number of policies in the tenancy: {policy_count}")

    if policy_limit > 0:
        policy_usage = (policy_count / policy_limit) * 100
        if policy_usage >= DEFAULT_THRESHOLD_PERCENTAGE:
            log_message(f"Policy usage is at {policy_usage:.2f}% of the allowed limit ({policy_count}/{policy_limit})\n")
        else:
            log_message(f"Policy usage is below threshold: {policy_usage:.2f}%\n")

# Function to find the tenancy's home region
def get_home_region(identity_client, tenancy_id):
    regions = identity_client.list_region_subscriptions(tenancy_id).data
    for region in regions:
        if region.is_home_region:
            return region.region_name
    return None

# Function to check service limits in the specified regions
def check_service_limits(signer, notification_topic_id, regions, threshold_percentage, policy_limit=None):
    try:
        identity_client = oci.identity.IdentityClient({}, signer=signer)

        tenancy_id = signer.tenancy_id
        compartment_id = tenancy_id

        if policy_limit is not None:
            check_policy_limits(identity_client, policy_limit, compartment_id)

        if regions == 'all':
            regions = identity_client.list_region_subscriptions(tenancy_id).data
            region_names = [r.region_name for r in regions]
        elif isinstance(regions, list):
            region_names = regions
        else:
            region_names = [regions]

        for region_name in region_names:
            log_message(f"Processing region: {region_name}")

            config = {"region": region_name}
            limits_client = oci.limits.LimitsClient(config, signer=signer)

            services = list_all_services(compartment_id, limits_client)
            availability_domains = list_availability_domains(identity_client, compartment_id)

            for service in services:
                service_name = service.name
                limit_definitions = get_all_limit_definitions(service_name, compartment_id, limits_client)

                for definition in limit_definitions:
                    limit_name = definition.name
                    scope_type = definition.scope_type

                    if definition.is_deprecated:
                        continue

                    limit_values = get_all_limit_values(service_name, compartment_id, limits_client)

                    for limit_value_obj in limit_values:
                        if limit_value_obj.name == limit_name:
                            service_limit = limit_value_obj.value

                            if scope_type == 'AD':
                                for ad in availability_domains:
                                    usage, available = get_resource_availability(
                                        service_name, limit_name, compartment_id, limits_client, ad, scope_type='AD'
                                    )

                                    if usage is not None and available is not None:
                                        log_usage_if_above_threshold(service_name, scope_type, ad, limit_name, service_limit, usage, available, threshold_percentage)
                            else:
                                usage, available = get_resource_availability(
                                    service_name, limit_name, compartment_id, limits_client, scope_type=scope_type
                                )

                                if usage is not None and available is not None:
                                    log_usage_if_above_threshold(service_name, scope_type, None, limit_name, service_limit, usage, available, threshold_percentage)

        send_notification(notification_topic_id, signer)

        return {"message": "Function executed successfully."}
    
    except Exception as ex:
        error_message = f"Function execution failed: {str(ex)}"
        log_message(error_message)
        add_to_error_log(error_message)
        return {"error": error_message}

# Main handler function for OCI Functions
def handler(ctx, data: io.BytesIO = None):
    try:
        # Parse the event input (expecting notification_topic_id, regions, policy_limit, and threshold_percentage in the event)
        event = json.loads(data.getvalue())
        notification_topic_id = event.get("notification_topic_id")
        regions = event.get("regions")  # User-specified regions as list or "all"
        threshold_percentage = event.get("threshold_percentage", DEFAULT_THRESHOLD_PERCENTAGE)  # Default to 90%
        policy_limit = event.get("policy_limit", DEFAULT_POLICY_LIMIT)  # Policy limit from input
        
        if not notification_topic_id:
            log_message("Error: Notification topic ID not provided.")
            return response.Response(
                ctx, response_data=json.dumps({"error": "Notification topic ID not provided"}), 
                headers={"Content-Type": "application/json"}
            )

        # Initialize Resource Principal authentication (signer)
        signer = oci.auth.signers.get_resource_principals_signer()

        # If no regions are provided, default to home region
        if not regions:
            identity_client = oci.identity.IdentityClient({}, signer=signer)
            regions = get_home_region(identity_client, signer.tenancy_id)
            #log_message(f"Defaulting to home region: {regions}")

        # Execute service limits and policy check logic
        resp = check_service_limits(signer, notification_topic_id, regions, threshold_percentage, policy_limit)

        return response.Response(
            ctx, response_data=json.dumps(resp),
            headers={"Content-Type": "application/json"}
        )
    
    except Exception as ex:
        error_message = f"Function execution failed: {str(ex)}"
        log_message(error_message)
        return response.Response(
            ctx, response_data=json.dumps({"error": error_message}),
            headers={"Content-Type": "application/json"}
        )

