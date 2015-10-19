import datetime
import hashlib
import json

from dateutil.parser import parse
from django.core.urlresolvers import reverse
from django.http import HttpResponse
from django.shortcuts import render_to_response
from django.template import RequestContext
from django.template.loader import render_to_string
from mongoengine.base import ValidationError

from crits.core.crits_mongoengine import EmbeddedSource, create_embedded_source, json_handler
from crits.core.handlers import build_jtable, jtable_ajax_list, jtable_ajax_delete
from crits.core.class_mapper import class_from_id
from crits.core.handlers import csv_export
from crits.core.user_tools import is_admin, user_sources, is_user_favorite
from crits.core.user_tools import is_user_subscribed
from crits.notifications.handlers import remove_user_from_notification
from crits.signatures.signature import Signature, SignatureType
from crits.services.handlers import run_triage, get_supported_services


def generate_signature_csv(request):
    """
    Generate a CSV file of the Signature information

    :param request: The request for this CSV.
    :type request: :class:`django.http.HttpRequest`
    :returns: :class:`django.http.HttpResponse`
    """

    response = csv_export(request,Signature)
    return response

def get_id_from_link_and_version(link, version):
    """
    Get the ObjectId from a link_id and version number.

    :param link: The link_id of the Signature.
    :type link: str
    :param version: The version number of the Signature.
    :type version: int
    :returns: None, ObjectId
    """

    signature = Signature.objects(link_id=link, version=version).only('id').first()
    if not signature:
        return None
    else:
        return signature.id

def get_signature_details(_id, analyst):
    """
    Generate the data to render the Signature details template.

    :param _id: The ObjectId of the Signature to get details for.
    :type _id: str
    :param analyst: The user requesting this information.
    :type analyst: str
    :returns: template (str), arguments (dict)
    """

    template = None
    sources = user_sources(analyst)
    if not _id:
        signature = None
    else:
        signature = Signature.objects(id=_id, source__name__in=sources).first()
    if not signature:
        template = "error.html"
        args = {'error': 'signature not yet available or you do not have access to view it.'}
    else:

        signature.sanitize("%s" % analyst)

        # remove pending notifications for user
        remove_user_from_notification("%s" % analyst, signature.id, 'Signature')

        # subscription
        subscription = {
                'type': 'Signature',
                'id': signature.id,
                'subscribed': is_user_subscribed("%s" % analyst,
                                                 'Signature', signature.id),
        }

        #objects
        objects = signature.sort_objects()

        #relationships
        relationships = signature.sort_relationships("%s" % analyst, meta=True)

        # relationship
        relationship = {
                'type': 'Signature',
                'value': signature.id
        }

        versions = len(Signature.objects(link_id=signature.link_id).only('id'))

        #comments
        comments = {'comments': signature.get_comments(),
                    'url_key': _id}

        #screenshots
        screenshots = signature.get_screenshots(analyst)

        # favorites
        favorite = is_user_favorite("%s" % analyst, 'Signature', signature.id)

        # services
        service_list = get_supported_services('Signature')

        # analysis results
        service_results = signature.get_analysis_results()

        args = {'service_list': service_list,
                'objects': objects,
                'relationships': relationships,
                'comments': comments,
                'favorite': favorite,
                'relationship': relationship,
                "subscription": subscription,
                "screenshots": screenshots,
                "versions": versions,
                "service_results": service_results,
                "signature": signature}

    return template, args

def generate_inline_comments(_id):
    """
    Generate the inline comments for Signature.

    :param _id: The ObjectId of the Signature to generate inline comments for.
    :type _id: str
    :returns: list
    """

    signature = Signature.objects(id=_id).first()
    if not signature:
        return []
    else:
        inlines = []
        for i in signature.inlines:
            html = render_to_string('inline_comment.html',
                                    {'username': i.analyst,
                                    'comment': i.comment,
                                    'date': i.date,
                                    'line': i.line,
                                    'signature': {'id': _id}})
            inlines.append({'line': i.line, 'html': html})
        return inlines

def generate_signature_versions(_id):
    """
    Generate a list of available versions for this Signature.

    :param _id: The ObjectId of the Signature to generate versions for.
    :type _id: str
    :returns: list
    """

    signature = Signature.objects(id=_id).only('link_id').first()
    if not signature:
        return []
    else:
        versions = []
        rvs = Signature.objects(link_id=signature.link_id).only('id',
                                                             'title',
                                                             'version',
                                                             'data')
        for rv in rvs:
            link = reverse('crits.signatures.views.signature_detail',
                           args=(rv.id,))
            versions.append({'title': rv.title,
                            'version': rv.version,
                            'data': rv.data,
                             'link': link})
        return versions

def generate_signature_jtable(request, option):
    """
    Generate the jtable data for rendering in the list template.

    :param request: The request for this jtable.
    :type request: :class:`django.http.HttpRequest`
    :param option: Action to take.
    :type option: str of either 'jtlist', 'jtdelete', or 'inline'.
    :returns: :class:`django.http.HttpResponse`
    """

    obj_type = Signature
    type_ = "signature"
    mapper = obj_type._meta['jtable_opts']
    if option == "jtlist":
        # Sets display url
        details_url = mapper['details_url']
        details_url_key = mapper['details_url_key']
        fields = mapper['fields']
        response = jtable_ajax_list(obj_type,
                                    details_url,
                                    details_url_key,
                                    request,
                                    includes=fields)
        return HttpResponse(json.dumps(response,
                                       default=json_handler),
                            content_type="application/json")
    if option == "jtdelete":
        response = {"Result": "ERROR"}
        if jtable_ajax_delete(obj_type,request):
            response = {"Result": "OK"}
        return HttpResponse(json.dumps(response,
                                       default=json_handler),
                            content_type="application/json")
    jtopts = {
        'title': "Signature",
        'default_sort': mapper['default_sort'],
        'listurl': reverse('crits.%ss.views.%s_listing' % (type_,
                                                            type_),
                           args=('jtlist',)),
        'deleteurl': reverse('crits.%ss.views.%s_listing' % (type_,
                                                              type_),
                             args=('jtdelete',)),
        'searchurl': reverse(mapper['searchurl']),
        'fields': mapper['jtopts_fields'],
        'hidden_fields': mapper['hidden_fields'],
        'linked_fields': mapper['linked_fields'],
        'details_link': mapper['details_link'],
        'no_sort': mapper['no_sort']
    }
    jtable = build_jtable(jtopts,request)
    jtable['toolbar'] = [
        {
            'tooltip': "'All Signatures'",
            'text': "'All'",
            'click': "function () {$('#signature_listing').jtable('load', {'refresh': 'yes'});}",
            'cssClass': "'jtable-toolbar-center'",
        },
        {
            'tooltip': "'New Signature'",
            'text': "'New'",
            'click': "function () {$('#signature_listing').jtable('load', {'refresh': 'yes', 'status': 'New'});}",
            'cssClass': "'jtable-toolbar-center'",
        },
        {
            'tooltip': "'In Progress Signatures'",
            'text': "'In Progress'",
            'click': "function () {$('#signature_listing').jtable('load', {'refresh': 'yes', 'status': 'In Progress'});}",
            'cssClass': "'jtable-toolbar-center'",
        },
        {
            'tooltip': "'Analyzed Signatures'",
            'text': "'Analyzed'",
            'click': "function () {$('#signature_listing').jtable('load', {'refresh': 'yes', 'status': 'Analyzed'});}",
            'cssClass': "'jtable-toolbar-center'",
        },
        {
            'tooltip': "'Deprecated Signatures'",
            'text': "'Deprecated'",
            'click': "function () {$('#signature_listing').jtable('load', {'refresh': 'yes', 'status': 'Deprecated'});}",
            'cssClass': "'jtable-toolbar-center'",
        },
        {
            'tooltip': "'Add Signature'",
            'text': "'Add Signature'",
            'click': "function () {$('#new-signature').click()}",
        },
    ]

    if option == "inline":
        return render_to_response("jtable.html",
                                  {'jtable': jtable,
                                   'jtid': '%s_listing' % type_,
                                   'button' : '%s_tab' % type_},
                                  RequestContext(request))
    else:
        return render_to_response("%s_listing.html" % type_,
                                  {'jtable': jtable,
                                   'jtid': '%s_listing' % type_},
                                  RequestContext(request))

def handle_signature_file(data, source_name, user=None,
                         description=None, title=None, data_type=None,
                         link_id=None, method='', reference='',
                         copy_rels=False, bucket_list=None, ticket=None):
    """
    Add Signature.

    :param data: The data of the Signature.
    :type data: str
    :param source_name: The source which provided this Signature.
    :type source_name: str,
                       :class:`crits.core.crits_mongoengine.EmbeddedSource`,
                       list of :class:`crits.core.crits_mongoengine.EmbeddedSource`
    :param user: The user adding the Signature.
    :type user: str
    :param description: Description of the Signature.
    :type description: str
    :param title: Title of the Signature.
    :type title: str
    :param data_type: Datatype of the Signature.
    :type data_type: str
    :param link_id: LinkId to tie this to another Signature as a new version.
    :type link_id: str
    :param method: The method of acquiring this Signature.
    :type method: str
    :param reference: A reference to the source of this Signature.
    :type reference: str
    :param copy_rels: Copy relationships from the previous version to this one.
    :type copy_rels: bool
    :param bucket_list: Bucket(s) to add to this Signature
    :type bucket_list: str(comma separated) or list.
    :param ticket: Ticket(s) to add to this Signature
    :type ticket: str(comma separated) or list.
    :returns: dict with keys:
              'success' (boolean),
              'message' (str),
              '_id' (str) if successful.
    """

    if not data or not title or not data_type:
        status = {
            'success':   False,
            'message':  'No data object, title, or data type passed in'
        }
        return status

    if not source_name:
        return {"success" : False, "message" : "Missing source information."}

    rdt = SignatureType.objects(name=data_type).first()
    if not rdt:
        status = {
            'success':   False,
            'message':  'Invalid data type passed in'
        }
        return status

    if len(data) <= 0:
        status = {
            'success':   False,
            'message':  'Data length <= 0'
        }
        return status

    # generate md5 and timestamp
    md5 = hashlib.md5(data).hexdigest()
    timestamp = datetime.datetime.now()
    
    # generate signature
    is_signature_new = False
    signature = Signature.objects(md5=md5).first()
    if not signature:
        signature = Signature()
        signature.created = timestamp
        signature.description = description
        signature.md5 = md5
        #signature.source = [source]
        signature.data = data
        signature.title = title
        signature.data_type = data_type
        is_signature_new = True
    
    # generate new source information and add to sample
    if isinstance(source_name, basestring) and len(source_name) > 0:
        source = create_embedded_source(source_name,
                                   date=timestamp,
                                   method=method,
                                   reference=reference,
                                   analyst=user)
        # this will handle adding a new source, or an instance automatically
        signature.add_source(source)
    elif isinstance(source_name, EmbeddedSource):
        signature.add_source(source_name, method=method, reference=reference)
    elif isinstance(source_name, list) and len(source_name) > 0:
        for s in source_name:
            if isinstance(s, EmbeddedSource):
                signature.add_source(s, method=method, reference=reference)
    
    #XXX: need to validate this is a UUID
    if link_id:
        signature.link_id = link_id
        if copy_rels:
            rd2 = Signature.objects(link_id=link_id).first()
            if rd2:
                if len(rd2.relationships):
                    signature.save(username=user)
                    signature.reload()
                    for rel in rd2.relationships:
                        # Get object to relate to.
                        rel_item = class_from_id(rel.rel_type, rel.rel_object_id)
                        if rel_item:
                            signature.add_relationship(rel_item,
                                                      rel.relationship,
                                                      rel_date=rel.relationship_date,
                                                      analyst=user)


    signature.version = len(Signature.objects(link_id=link_id)) + 1

    if bucket_list:
        signature.add_bucket_list(bucket_list, user)

    if ticket:
        signature.add_ticket(ticket, user);

    # save signature
    signature.save(username=user)

    # run signature triage
    if is_signature_new:
        signature.reload()
        run_triage(signature, user)

    status = {
        'success':      True,
        'message':      'Uploaded signature',
        '_id':          signature.id,
        'object':       signature
    }

    return status

def update_signature_type(_id, data_type, analyst):
    """
    Update the Signature data type.

    :param _id: ObjectId of the Signature to update.
    :type _id: str
    :param data_type: The data type to set.
    :type data_type: str
    :param analyst: The user updating the data type.
    :type analyst: str
    :returns: dict with keys "success" (boolean) and "message" (str) if failed.
    """

    signature = Signature.objects(id=_id).first()
    data_type = SignatureType.objects(name=data_type).first()
    if not data_type:
        return None
    else:
        signature.data_type = data_type.name
        try:
            signature.save(username=analyst)
            return {'success': True}
        except ValidationError, e:
            return {'success': False, 'message': str(e)}

def update_signature_highlight_comment(_id, comment, line, analyst):
    """
    Update a highlight comment.

    :param _id: ObjectId of the Signature to update.
    :type _id: str
    :param comment: The comment to add.
    :type comment: str
    :param line: The line this comment is associated with.
    :type line: str, int
    :param analyst: The user updating the comment.
    :type analyst: str
    :returns: dict with keys "success" (boolean) and "message" (str) if failed.
    """

    signature = Signature.objects(id=_id).first()
    if not signature:
        return None
    else:
        for highlight in signature.highlights:
            if highlight.line == int(line):
                highlight.comment = comment
                try:
                    signature.save(username=analyst)
                    return {'success': True}
                except ValidationError, e:
                    return {'success': False, 'message': str(e)}
        return {'success': False, 'message': 'Could not find highlight.'}

def update_signature_highlight_date(_id, date, line, analyst):
    """
    Update a highlight date.

    :param _id: ObjectId of the Signature to update.
    :type _id: str
    :param date: The date to set.
    :type date: str
    :param line: The line this date is associated with.
    :type line: str, int
    :param analyst: The user updating the date.
    :type analyst: str
    :returns: dict with keys "success" (boolean) and "message" (str) if failed.
    """

    signature = Signature.objects(id=_id).first()
    if not signature:
        return None
    else:
        for highlight in signature.highlights:
            if highlight.line == int(line):
                highlight.line_date = parse(date, fuzzy=True)
                try:
                    signature.save(username=analyst)
                    return {'success': True}
                except ValidationError, e:
                    return {'success': False, 'message': str(e)}
        return {'success': False, 'message': 'Could not find highlight.'}

def new_inline_comment(_id, comment, line_num, analyst):
    """
    Add a new inline comment.

    :param _id: ObjectId of the Signature to update.
    :type _id: str
    :param comment: The comment to add.
    :type comment: str
    :param line_num: The line this comment is associated with.
    :type line_num: str, int
    :param analyst: The user adding this comment.
    :type analyst: str
    :returns: dict with keys:
              "success" (boolean),
              "message" (str),
              "line" (int),
              "html" (str)
    :raises: ValidationError
    """

    signature = Signature.objects(id=_id).first()
    signature.add_inline_comment(comment, line_num, analyst)
    try:
        signature.save(username=analyst)
        html = render_to_string('inline_comment.html',
                                {'username': analyst,
                                 'comment': comment,
                                 'date': datetime.datetime.now(),
                                 'line': line_num,
                                 'signature': {'id': _id}})
        return {'success': True,
                'message': 'Comment for line %s added successfully!' % line_num,
                'inline': True,
                'line': line_num,
                'html': html,
                }
    except ValidationError, e:
        return e

def new_highlight(_id, line_num, line_data, analyst):
    """
    Add a new highlight.

    :param _id: ObjectId of the Signature to update.
    :type _id: str
    :param line_num: The line to highlight.
    :type line_num: str, int
    :param line_data: The data on this line.
    :type line_data: str
    :param analyst: The user highlighting this line.
    :type analyst: str
    :returns: dict with keys "success" (boolean) and "html" (str)
    :raises: ValidationError
    """

    signature = Signature.objects(id=_id).first()
    signature.add_highlight(line_num, line_data, analyst)
    try:
        signature.save(username=analyst)
        html = render_to_string('signature_highlights.html',
                                {'signature': {'id': _id,
                                              'highlights': signature.highlights}})
        return {'success': True,
                'html': html,
                }
    except ValidationError, e:
        return e

def delete_highlight(_id, line_num, analyst):
    """
    Delete a highlight from Signature.

    :param _id: The ObjectId of the Signature to update.
    :type _id: str
    :param line_num: Line number of the highlight to delete.
    :type line_num: str, int
    :param analyst: The user deleting this highlight.
    :type analyst: str
    :returns: dict with keys "success" (boolean) and "html" (str)
    """

    signature = Signature.objects(id=_id).first()
    highlights = len(signature.highlights)
    signature.remove_highlight(line_num, analyst)
    if len(signature.highlights) < highlights:
        try:
            signature.save(username=analyst)
            html = render_to_string('signature_highlights.html',
                                    {'signature': {'id': _id,
                                                'highlights': signature.highlights}})
            return {'success': True,
                    'html': html,
                    }
        except ValidationError, e:
            return e
    else:
        return {'success': False}

def delete_signature(_id, username=None):
    """
    Delete Signature from CRITs.

    :param _id: The ObjectId of the Signature to delete.
    :type _id: str
    :param username: The user deleting this Signature.
    :type username: str
    :returns: bool
    """

    if is_admin(username):
        signature = Signature.objects(id=_id).first()
        if signature:
            signature.delete(username=username)
            return True
        else:
            return False
    else:
        return False

def add_new_signature_type(data_type, analyst):
    """
    Add a new Signature datatype to CRITs.

    :param data_type: The new datatype to add.
    :type data_type: str
    :param analyst: The user adding the new datatype.
    :type analyst: str
    :returns: bool
    """

    data_type = data_type.strip()
    try:
        signature_type = SignatureType.objects(name=data_type).first()
        if signature_type:
            return False
        signature_type = SignatureType()
        signature_type.name = data_type
        signature_type.save(username=analyst)
        return True
    except ValidationError:
        return False