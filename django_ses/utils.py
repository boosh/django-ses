import base64
import logging

try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO

from django.core.exceptions import ImproperlyConfigured
from django.utils.encoding import smart_str

logger = logging.getLogger(__name__)

class BounceMessageVerifier(object):
    """
    A utility class for validating bounce messages

    See: http://docs.amazonwebservices.com/sns/latest/gsg/SendMessageToHttp.verify.signature.html
    """

    def __init__(self, bounce_dict):
        """
        Creates a new bounce message from the given dict.
        """
        self._data = bounce_dict
        self._verified = None

    def is_verified(self):
        """
        Verifies an SES bounce message.

        """
        if self._verified is None:
            signature = self._data.get('Signature')
            if not signature:
                self._verified = False
                return self._verified

            # Decode the signature from base64
            signature = base64.b64decode(signature)

            # Get the message to sign
            sign_bytes = self._get_bytes_to_sign()
            if not sign_bytes:
                self._verified = False
                return self._verified

            certificate = self._get_cert()
            if not certificate:
                self._verified = False
                return self._verified

            # Extract the public key
            pkey = certificate.get_pubkey()

            # Use the public key to verify the signature.
            pkey.verify_init()
            pkey.verify_update(sign_bytes)
            verify_result = pkey.verify_final(signature)

            self._verified = verify_result == 1

        return self._verified

    def _get_cert(self):
        """
        Retrieves the certificate used to sign the bounce message.

        TODO: Cache the certificate based on the cert URL so we don't have to
        retrieve it for each bounce message. *We would need to do it in a
        secure way so that the cert couldn't be overwritten in the cache*
        """
        cert_url = self._data.get('SigningCertURL')
        # TODO: Only load certificates from a certain domain?
        #       Without some kind of trusted domain check, any old joe could
        #       craft a bounce message and sign it using his own certificate
        #       and we would happily load and verify it.
        if not cert_url:
            return None

        try:
            import requests
        except ImportError:
            raise ImproperlyConfigured("requests is required for bounce message verification.")

        try:
            import M2Crypto 
        except ImportError:
            raise ImproperlyConfigured("M2Crypto is required for bounce message verification.")

        # We use requests because it verifies the https certificate
        # when retrieving the signing certificate. If https was somehow
        # hijacked then all bets are off.
        response = requests.get(cert_url)
        if response.status_code != 200:
            logger.warning('Could not download certificate from %s: "%s"', cert_url, response.status_code)
            return None

        # Handle errors loading the certificate.
        # If the certificate is invalid then return
        # false as we couldn't verify the message.
        try:
            return M2Crypto.X509.load_cert_string(response.content)
        except M2Crypto.X509.X509Error, e:
            logger.warning('Could not load certificate from %s: "%s"', cert_url, e)
            return None

    def _get_bytes_to_sign(self):
        """
        Creates the message used for signing SNS notifications.
        This is used to verify the bounce message when it is received.
        """

        # Depending on the message type the fields to add to the message
        # differ so we handle that here.
        msg_type = self._data.get('Type')
        if msg_type == 'Notification':
            fields_to_sign = [
                'Message',
                'MessageId',
                'Subject',
                'Timestamp',
                'TopicArn',
                'Type',
            ]
        elif (msg_type == 'SubscriptionConfirmation' or
              msg_type == 'UnsubscribeConfirmation'):
            fields_to_sign = [
                'Message',
                'MessageId',
                'SubscribeURL',
                'Timestamp',
                'Token',
                'TopicArn',
                'Type',
            ]
        else:
            # Unrecognized type
            logger.warning('Unrecognized SNS message Type: "%s"', msg_type)
            return None
        
        outbytes = StringIO()
        for field_name in fields_to_sign:
            field_value = smart_str(self._data.get(field_name, ''),
                                    errors="replace")
            if field_value:
                outbytes.write(field_name)
                outbytes.write("\n")
                outbytes.write(field_value)
                outbytes.write("\n")
         
        return outbytes.getvalue()

def verify_bounce_message(msg):
    u"""
    Verify an SES/SNS bounce notification message.
    """
    verifier = BounceMessageVerifier(msg)
    return verifier.is_verified()
